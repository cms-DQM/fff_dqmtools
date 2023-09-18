#!/usr/bin/env python3

import sys, os


def prepare_imports():
    # minihack
    sys.path.append("/opt/hltd/python")
    sys.path.append("/opt/hltd/lib")

    thp = os.path.dirname(__file__)
    sys.path.append(os.path.join(thp, "./"))
    sys.path.append(os.path.join(thp, "./lib"))

    # sys.path.insert(0, os.path.join(thp, "./lib/gevent-1.0.1-py2.6-linux-x86_64.egg"))
    # sys.path.insert(0, os.path.join(thp, "./lib/greenlet-0.4.5-py2.6-linux-x86_64.egg"))
    # sys.path.insert(0, os.path.join(thp, "./lib/ws4py-0.3.4-py2.6.egg"))
    # sys.path.insert(0, os.path.join(thp, "./lib/ws4py"))

    # bytecode only creates problems with distribution
    # and we don't benefit much from the performance gain anyway

    if "PYTHONDONTWRITEBYTECODE" not in os.environ:
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        sys.dont_write_bytecode = True


prepare_imports()

import signal, pwd, grp
import logging, json
import subprocess
import collections
import hashlib
import gevent

# calculate installation key, used in locking
__ipath__ = os.path.dirname(os.path.realpath(__file__))
__ipkey__ = hashlib.sha1(__ipath__.encode("utf-8")).hexdigest()[:8]

log = logging.getLogger(__name__)


class LogCaptureHandler(logging.StreamHandler):
    """
    A handler which output to stderr,
    but keeps N numbers in history.
    """

    log_format = logging.Formatter(
        "%(asctime)s: %(name)-20s - %(levelname)-8s - %(message)s"
    )

    def __init__(self):
        logging.StreamHandler.__init__(self)

        self.setLevel(logging.INFO)
        self.setFormatter(self.log_format)

        # history buffer
        self.buffer = collections.deque(maxlen=1 * 1024)

    def retrieve(self):
        return "\n".join(self.buffer)

    def retrieve_json(self):
        return list(self.buffer)

    def direct_write(self, line):
        # used by the subprocess
        # to directory write to it
        self.buffer.append(line)
        self.stream.write(line)
        self.flush()

    def emit(self, record):
        msg = self.format(record)
        self.direct_write(msg + "\n")

    @classmethod
    def create_logger_subprocess(cls, module_name):
        # prepare logging for the applet
        logger = logging.getLogger(module_name)
        handler = logging.StreamHandler()
        handler.setFormatter(cls.log_format)
        handler.setLevel(logging.INFO)

        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        return logger


class Server(object):
    def __init__(self, opt):
        self.opts = opt

        self.loggers = {}

    def config_log(self, name):
        if name not in self.loggers:
            log_capture = LogCaptureHandler()

            l = logging.getLogger(name)
            l.addHandler(log_capture)
            l.setLevel(logging.INFO)

            self.loggers[name] = (l, log_capture)

        return self.loggers[name][0]

    def get_loggers(self):
        return dict(self.loggers)

    def run(self):
        applets = self.opts["applets"]
        log.info("Found applets: %s", applets)

        greenlets = []
        for applet in applets:
            log.info("Starting applet: %s", applet)

            # spawn the logger with the same name
            logger = self.config_log(applet)

            module_name = "applets." + applet
            _mod_package = __import__(module_name)
            mod = getattr(_mod_package, applet)

            kwargs = {
                "server": self,
                "opts": self.opts,
                "logger": logger,
                "name": applet,
                "module_name": module_name,
                "module": mod,
            }
            greenlets.append(gevent.spawn(mod.__run__, **kwargs))

            # a small delay
            # it is not necessary, but helps to debug logs and failures
            gevent.sleep(0.5)

        gevent.joinall(greenlets, raise_error=True)


def get_lock_key(name):
    key = __ipkey__
    return "%s.%s" % (
        key,
        name,
    )


def lock_wrapper(f):
    # decorator which requires applet to get the lock
    # so you can't run multiple instances of it

    # use a named socket check if we are running
    # this is very clean and atomic and leave no files
    # from: http://stackoverflow.com/a/7758075
    def _socket_lock(pname):
        import gevent.socket as socket

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind("\0" + pname)
            return sock
        except socket.error:
            return None

    def wrapper(*kargs, **kwargs):
        logger = kwargs["logger"]

        lkey = get_lock_key(kwargs["name"])
        lock = _socket_lock(lkey)
        if lock is None:
            raise Exception(
                "Could not get the lock for %s, path: %s" % (lkey, __ipath__)
            )

        kwargs["lock_socket"] = lock
        kwargs["lock_key"] = lkey

        logger.info("Acquired lock: %s", lkey)
        f(*kargs, **kwargs)

    return wrapper


def setuid(uid, gid):
    try:
        if isinstance(uid, str):
            uid = pwd.getpwnam(
                uid
            ).pw_uid  # get user id from user name https://docs.python.org/3/library/pwd.html

        if isinstance(gid, str):
            gid = grp.getgrnam(gid).gr_gid  # get group id from group name

        os.setgid(gid)
        os.setuid(uid)
    except Exception as e:
        print(e)
        sys.stderr.write("Can't set the uid/gid: %s\n" % str(e))
        sys.stderr.flush()


def _select_readlines(fd):
    import gevent.select as select
    import fcntl

    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
    lbuf = []

    while True:
        # silly, it's only one fd, but we have to do it
        # for gevent to task switch
        rlist, wlist, xlist = select.select([fd], [], [])
        assert fd in rlist

        buf = os.read(fd, 4096)
        buf = buf.decode("utf-8")
        if len(buf) == 0:
            if lbuf:
                yield "".join(lbuf)
            return

        lbuf.append(buf)
        if "\n" in buf:
            # re-split lines
            lbuf = "".join(lbuf).split("\n")

            # pop all except the last line
            while len(lbuf) > 1:
                yield lbuf.pop(0) + "\n"


def _pr_set_deathsig():
    # ensure the child dies if we are SIGKILLED
    import ctypes

    libc = ctypes.CDLL("libc.so.6")
    PR_SET_PDEATHSIG = 1
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)


def _execute_module(module_name, logger, append_environ, **wrapper_kwargs):
    # find the interpreter and the config file
    args = [sys.executable, "-m", "fff_dqmtools", module_name]

    env = os.environ.copy()
    env["PYTHONPATH"] = __ipath__ + ":" + env.get("PYTHONPATH", "")

    for k, v in append_environ.items():
        env[k] = v

    logger.info("Spawning process with args: %s", repr(args))

    def preexec():
        # setting setuid clears PR_SET_PDEATHSIG
        # so do it before setting it
        if "uid" in wrapper_kwargs:
            setuid(wrapper_kwargs["uid"], wrapper_kwargs["gid"])

        _pr_set_deathsig()

    proc = subprocess.Popen(
        args,
        shell=False,
        bufsize=1,
        close_fds=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=preexec,
        env=env,
    )

    # unfortunately, we are using super old version of gevent
    # so we do this select-way
    fd = proc.stdout.fileno()

    try:
        for line in _select_readlines(fd):
            for handler in logger.handlers:
                if hasattr(handler, "direct_write"):
                    handler.direct_write(line)

        return proc.wait()
    finally:
        proc.stdout.close()


def _execute_child():
    kwargs = json.loads(os.getenv("FFF_DQMTOOLS_CHILD"))

    # prepare logging for the applet
    logger = LogCaptureHandler.create_logger_subprocess(kwargs["name"])
    logger.info("Synchronized process (child) %d", os.getpid())
    kwargs["logger"] = logger
    kwargs["fork"] = True

    # check for setuid
    name = pwd.getpwuid(os.getuid())[0]
    logger.info(
        "Running as uid=%s(%s) gid=%s groups=(%s)",
        os.getuid(),
        name,
        os.getgid(),
        os.getgroups(),
    )

    # just run the function and exit
    applet = kwargs["name"]
    module_name = "applets." + applet
    _mod_package = __import__(module_name)
    mod = getattr(_mod_package, applet)

    ret = mod.__run__(**kwargs)
    sys.exit(ret)


def fork_wrapper_decorate(func, **wrapper_kwargs):
    # this function (decorator) has two entries:
    # 1. then the applet is declared (but not launched).
    # in this case we return a wrapper function which will:
    #   - launch a subprocess (with the actual function)
    #   - copy whatever output we get to the assigned logger
    #   - restart on failure
    #
    # 2. then we reached this via subprocess child,
    # in launch the function (we do not decorate it, and call
    # sys.exit() right after.

    # the only reason for having this re-entry is to
    # have nice process names in ps auxf

    ## first check if we are in a child
    if os.getenv("FFF_DQMTOOLS_CHILD") is not None:
        return func
    else:

        def execute_loop(**kwargs):
            # get the logger, it is set up by Server()
            logger = kwargs["logger"]

            module_name = kwargs["module_name"]
            print(kwargs["module"])

            kwopts = {
                "opts": kwargs["opts"],
                "name": kwargs["name"],
                "module_name": module_name,
            }

            env = {}
            env["FFF_DQMTOOLS_CHILD"] = json.dumps(kwopts)

            while True:
                ec = _execute_module(module_name, logger, env, **wrapper_kwargs)
                if ec == 0:
                    logger.info("Process %s exitted, error code: %d", module_name, ec)
                    break

                logger.warning(
                    "Process %s exitted, error code: %d, will be restarted.",
                    module_name,
                    ec,
                )
                gevent.sleep(15)

        # since we act as a decorator
        # we have to return a function a new function
        return execute_loop


def fork_wrapper(__name_mod, **wrapper_kwargs):
    return lambda f: fork_wrapper_decorate(f, **wrapper_kwargs)


def detach(logfile, pidfile):
    # do the double fork
    pid = os.fork()
    if pid != 0:
        sys.exit(0)

    os.setsid()

    fl = open(logfile, "a")
    os.dup2(fl.fileno(), sys.stdin.fileno())
    os.dup2(fl.fileno(), sys.stdout.fileno())
    os.dup2(fl.fileno(), sys.stderr.fileno())
    fl.close()

    pid = os.fork()
    if pid != 0:
        sys.exit(0)

    if pidfile:
        f = open(pidfile, "w")
        f.write("%d\n" % os.getpid())
        f.close()


# this is no longer used
# this process only acts as a supervisor, if it crashes - let it crash
##def run_supervised(f):
##    def clear_fds():
##        try:
##            maxfd = os.sysconf("SC_OPEN_MAX")
##        except (AttributeError, ValueError):
##            maxfd = 1024
##
##        os.closerange(3, maxfd)
##
##    try:
##        f()
##    except KeyboardInterrupt:
##        log.warning("Daemon failure, keyboard interrupt:", exc_info=True)
##    except:
##        log.warning("Daemon failure, will restart in 15s.:", exc_info=True)
##
##        # wait 30s so we don't restart too often
##        time.sleep(15)
##        clear_fds()
##
##        args = [sys.executable] + sys.argv
##        os.execv(sys.executable, args)

if __name__ == "__main__":
    if os.getenv("FFF_DQMTOOLS_CHILD") is not None:
        _execute_child()
        sys.exit(1)  # child should exit itself

    default_applets = [
        "fff_web",
        "fff_selftest",
        "fff_logcleaner",
        "fff_logcleaner_gzip",
        "fff_filemonitor",
        "fff_deleter_c2a06_01_01",
        "fff_deleter_playback_c2a06_03_01",
        "fff_deleter_lookarea_c2a06_05_01",
        "fff_deleter_minidaq_c2a06_05_01",
        "fff_deleter_minidaq_cms904",
        "fff_simulator",
        "analyze_files",
        "analyze_files_lookarea_c2a06_05_01",
        "analyze_releases",
    ]

    config_web_secret = "changeme"
    try:
        sys.path.append("/var/lib/fff_dqmtools/")
        import fff_config

        config_web_secret = fff_config.web_secret
    except:
        pass
    opt = {
        "do_foreground": False,
        "path": "/tmp/dqm_monitoring/",
        "applets": default_applets,
        "logfile": "/var/log/fff_dqmtools.log",
        "pidfile": "/var/run/fff_dqmtools.pid",
        "hltd_logfile": "/var/log/hltd/hltd.log",
        "anelastic_logfile": "/var/log/hltd/anelastic.log",
        "web.db": "/var/lib/fff_dqmtools/db.20171027.sqlite3",
        "web.port": 9215,
        "web.secret": config_web_secret,
        "web.secret_name": "selenium-secret-secret",
        "cmssw_path_playback": "/dqmdata/dqm_cmssw/current_playback",
        "cmssw_path_production": "/dqmdata/dqm_cmssw/current_production",
        "dqm_clients_subdir": "/src/DQM/Integration/python/clients/",
        "hltd_clients_path": "/etc/appliance/dqm_resources/",
        "deleter.ramdisk": "/fff/ramdisk/",
        "deleter.tag": "fff_deleter",
        "deleter.fake": False,
        "simulator.conf": "/etc/fff_simulator_dqmtools.conf",
    }

    key_types = {
        "logfile": str,
        "pidfile": str,
        "hltd_logfile": str,
        "anelastic_logfile": str,
        "web.port": int,
        "web.db": str,
        "web.secret": str,
        "web.secret_name": str,
        "deleter.ramdisk": str,
        "deleter.tag": str,
        "deleter.fake": bool,
        "simulator.conf": str,
    }

    import fff_cluster

    c = fff_cluster.get_node()

    arg = sys.argv[1:]
    while arg:
        a = arg.pop(0)
        if a == "--foreground":
            opt["do_foreground"] = True
            continue

        if a == "--path":
            opt["path"] = arg.pop(0)
            continue

        if a == "--applets":
            opt["applets"] = arg.pop(0).split(",")
            continue

        if a.startswith("--") and a[2:] in key_types:
            key = a[2:]
            t = key_types[key]
            opt[key] = t(arg.pop(0))
            continue

        sys.stderr.write("Invalid parameter: %s.\n" % a)
        sys.stderr.flush()
        sys.exit(1)

    if not opt["do_foreground"]:
        detach(opt["logfile"], opt["pidfile"])

        args = [sys.executable] + sys.argv + ["--foreground"]
        os.execv(sys.executable, args)

    srv = Server(opt)

    # reconfigure our log
    # we have everything set up for it
    log = srv.config_log("root")

    # join the mega-event-loop
    log.info("Service started, pid is %d.", os.getpid())
    srv.run()
