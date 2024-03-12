# The FFF Simulator (previously called roll script) simulates the behaviour of
# the DAQ system for DQM.
#
# It copies a predefined set of streamer files and pb files to the ramdisk at
# a constant rate, simulating consecutive runs, each with multiple lumisections.
#
# The settings of the DAQ Simulator are defined in /etc/fff_simulator_dqmtools.conf
# The most important setting is obviously the location of the run to simulate.
# More information on the settings can be found in the configuration file.

import os
import re
import sys
import time
import json
import shutil
import socket
import logging

from applets.fff_filemonitor import atomic_create_write


# usually atomic_create_write writes files with 0600 mask.
# we need a bit more readable files instead.
def atomic_write(filename, content):
    return atomic_create_write(filename, content, mode=0o644)


log = logging.getLogger("fff_simulator")


class SimulatorRun(object):
    """A class to represent an active run.

    Run number should already be allocated by the manager.

    The class shouldn't handle operational errors (ie run directory already exists),
    but rather just crash (or enter 'error' state).

    This is easier to debug and to handle.
    """

    def __init__(self, manager, config, kwargs):
        self.manager = manager
        self.config = config
        self.report_directory = kwargs["opts"]["path"]
        self.control_socket_name = kwargs["lock_key"]

        self.next_lumi_index = 1
        self.lumi_backlog = []
        self.time_lumi_started = 0

        self.write_state("init")

        import gevent.event

        self.control_event = gevent.event.Event()

    def write_state(self, next_state=None):
        """
        Internal function which updates the current state of the run.
        It's called every state transition.

        Additionally it writes ./status and DQM^2 report files.
        """

        if next_state is not None:
            self.state = next_state

        # now write the state if possible
        if not hasattr(self, "run_directory"):
            return

        status = {}
        extra = {}

        status["state"] = self.state
        status["config"] = self.config
        status["socket_name"] = self.control_socket_name

        if hasattr(self, "st_current_lumi"):
            status["ls"] = self.st_current_lumi
            extra["ls_map"] = self.st_current_map

        status["extra"] = extra
        status_fn = os.path.join(self.run_directory, "status")
        atomic_write(status_fn, json.dumps(status, indent=2))

        # whatever happens now will only be written into dqm^2
        if hasattr(self, "streams_found"):
            extra["streams_found"] = self.streams_found

        if os.path.exists(self.report_directory):
            status["sequence"] = 0
            status["hostname"] = socket.gethostname()
            status["tag"] = __name__
            status["run"] = self.config["run"]
            status["pid"] = os.getpid()
            status["type"] = "dqm-playback"
            status["_id"] = "dqm-playback-%s-%s-run%d" % (
                status["hostname"],
                status["tag"],
                status["run"],
            )

            final_fp = os.path.join(self.report_directory, status["_id"] + ".jsn")
            body = json.dumps(status, indent=None)
            atomic_create_write(final_fp, body)

            log.info("Made report file: %s", final_fp)

    def run_unsafe(self):
        """
        The 'main' code of the run.
        It is 'unsafe' because it might throw an exception.

        'run()' is the 'safe' version of this method.
        """

        # create some directories
        while self.state == "init":
            self.discover_files()
            self.create_global_file()
            self.create_run_directory()
            self.write_state("running")

        # run stuff
        while self.state == "running":
            self.start_new_lumisection()
            self.write_state()

            if self.next_lumi_index > self.config["number_of_ls"]:
                self.write_state("eor")

            self.control_event.clear()
            self.control_event.wait(timeout=self.config["lumi_timeout"])

        # write eor
        while self.state == "eor":
            self.create_eor()
            self.write_state("stopped")

    def control(self, line, write_f):
        """Called from outside this class to control the state (run_unsafe()).

        It is usually and most likely called outside this class
        from another 'greenlet' while 'run()' is in progress.

        While it's not necessary to synchronize gevent threads,
        self.control_event still does some synchronization (to interrupt transition timeout).

        """

        def send(txt):
            write_f("run%d: %s\n" % (self.config["run"], txt))

        if line == "status":
            send("State: %s" % (self.state))

            if hasattr(self, "st_current_lumi"):
                send(
                    "Current lumi: %d" % (self.st_current_lumi),
                )
                send("Data files from %s:" % (self.config["source"],))
                for stream, file in self.st_current_map.items():
                    send("  %s -> %s" % (stream, file[0]))

        elif line == "restart" or line == "next_run":
            st = self.state
            if st == "running":
                self.write_state("eor")
                self.control_event.set()
                send("ok: changed state to eor")
            else:
                send("error: invalid state %s" % st)

        elif line == "next_lumi":
            st = self.state
            if st == "running":
                self.time_lumi_started = 0
                self.control_event.set()
                send("ok: next lumi will be %d" % self.next_lumi_index)
            else:
                send("error: invalid state %s" % st)

        else:
            send("error: unknown command")

    def run(self):
        """Safe version of run_unsafe().

        Should be called once we are ready to run.
        """

        try:
            self.run_unsafe()
        except:
            log.error("Got simulator error.", exc_info=True)
            self.state = "error"
            try:
                self.write_state("error")
            except:
                pass

    def file_ok(self, fp):
        """Checks if files are okay for re-copy.

        See make_copy() for details.
        """

        if (".deleted" in fp) or (not os.path.exists(fp)) or (os.stat(fp).st_size == 0):
            return False

        return True

    def make_copy(self, source, dest):
        """Performs a smart copy.

        The logic is:
          - if file wasn't copied to the dest directory -> copy it from source
          - if file exists in the dest directory (but with a different name) -> copy it from the old file

        The idea is to speed up copying if _files_ are outside the ramdisk.
        If playback files are on ramdisk, this has no effect.
        """

        if not hasattr(self, "_copy_map"):
            self._copy_map = {}

        cached_copy = self._copy_map.get(source, None)
        if cached_copy and self.file_ok(cached_copy):
            actual_source = cached_copy
            log.info("COPY*: %s -> %s", source, dest)
        else:
            actual_source = source
            log.info("COPY: %s -> %s", source, dest)

        shutil.copyfile(actual_source, dest)
        self._copy_map[source] = dest

    def discover_files(self):
        re_pattern = re.compile(
            r"run([0-9]+)_ls([0-9]+)_stream([A-Za-z0-9]+)_([A-Za-z0-9_-]+)\.jsn"
        )
        self.streams_found = {}

        files_found = set()
        run_found = None

        log.info("Scanning %s to find files.", self.config["source"])
        for f in sorted(os.listdir(self.config["source"])):
            r = re_pattern.match(f)
            if r:
                run, lumi, stream, stream_source = r.groups()
                run, lumi = int(run), int(lumi)

                if run_found is None:
                    run_found = run
                elif run_found != run:
                    # raise Exception("Files from multiple runs are not (yet) supported for as playback input.")
                    pass

                # remap stream if set
                stream_orig = stream
                remap = self.config.get("stream_remap", {})
                if stream in remap:
                    stream = remap[stream]
                    if stream not in self.streams_found:
                        log.info(
                            "Stream %s will be converted into stream %s",
                            stream_orig,
                            stream,
                        )

                files_found.add(f)
                stream_dct = self.streams_found.setdefault(stream, {"lumi_files": []})
                stream_dct["lumi_files"].append(
                    (
                        f,
                        stream_source,
                    )
                )

        if run_found is None:
            raise Exception("Playback files not found.")

        log.info(
            "Found %d files for playback run %d", len(files_found), self.config["run"]
        )
        log.info("Found %d streams, details:", len(self.streams_found))
        for stream in sorted(self.streams_found.keys()):
            stream_dct = self.streams_found[stream]
            log.info(
                "  found %d files for stream %s", len(stream_dct["lumi_files"]), stream
            )
            for file, source in stream_dct["lumi_files"]:
                log.info("    stream %s file %s", stream, file)

    def create_run_directory(self):
        rd = os.path.join(self.config["ramdisk"], "run%d" % self.config["run"])
        os.makedirs(rd, 0o755)
        log.info("Created run directory: %s" % rd)
        self.run_directory = rd

        # link this directory
        link_path = os.path.join(self.config["ramdisk"], "current")
        if os.path.lexists(link_path):
            os.unlink(link_path)

        link_content = os.path.relpath(self.run_directory, self.config["ramdisk"])
        os.symlink(link_content, link_path)

        # also write the run number to /var/run/fff_dqmtools/fff_simulator_run
        # so the number persists in case of a reboot
        run_write_file = self.config.get("run_write_file", None)
        if run_write_file:
            try:
                atomic_write(run_write_file, str(self.config["run"]))
            except:
                log.warning(
                    "Error writing the run number to the persistant storage.",
                    exc_info=True,
                )

        # create config file (for book-keeping)
        cf = os.path.join(rd, "config")
        atomic_write(cf, json.dumps(self.config, indent=2))

        # Now the famous Atanas hack to give inotify time to work correctly
        import gevent

        gevent.sleep(1)

    def create_global_file(self):
        # Creates the hidden .run*.global run file on the ramdisk.
        # run_unique_key value - https://github.com/cms-sw/cmssw/pull/33644#issuecomment-840511487
        # like self.config["run_unique_key"] = '4e94e771-add6-41be-8683-c5f6a7a9ed1f'
        file_name = ".run%d.global" % self.config["run"]
        full_name = os.path.join(self.config["ramdisk"], file_name)

        run_unique_key = "4e94e771-add6-41be-8683-c5f6a7a9ed1f"
        if "run_unique_key" in self.config:
            run_unique_key = self.config["run_unique_key"]

        body = "run_key = %s\n" % self.config["run_key"]
        body += "run_unique_key = %s\n" % run_unique_key

        atomic_write(full_name, body)
        log.info("Created hidden .global run file %s" % full_name)

    def create_eor(self):
        file_name = "run%d_ls0000_EoR.jsn" % self.config["run"]
        full_name = os.path.join(self.run_directory, file_name)
        atomic_write(full_name, "")
        log.info("Wrote EoR (end of run) file: %s" % full_name)

    def start_new_lumisection(self):
        run = self.config["run"]

        play_lumi = self.next_lumi_index
        self.next_lumi_index += 1
        self.time_lumi_started = time.time()

        log.info("Start copying playback run/lumi %d/%d", run, play_lumi)

        # same if for the status
        self.st_current_lumi = play_lumi
        self.st_current_map = {}

        # helpers to get full path for filename
        def input_join(f):
            return os.path.join(self.config["source"], f)

        def output_join(f):
            return os.path.join(self.run_directory, f)

        written_files = set()
        if play_lumi not in self.config["lumi_to_skip"]:
            # copy all the files for this lumi

            for stream, stream_dct in self.streams_found.items():
                # calculate which file goes here
                files = stream_dct["lumi_files"]

                jsn_orig_fn, stream_source = files[(play_lumi - 1) % len(files)]
                jsn_play_fn = "run%06d_ls%04d_stream%s_%s.jsn" % (
                    run,
                    play_lumi,
                    stream,
                    stream_source,
                )

                self.st_current_map[stream] = input_join(jsn_orig_fn)

                # read the original file name, for copying
                with open(input_join(jsn_orig_fn), "r") as f:
                    jsn_data = json.load(f)
                    dat_orig_fn = jsn_data["data"][3]
                    dat_orig_ext = os.path.splitext(dat_orig_fn)[1]

                # define dat filename
                dat_play_fn = "run%06d_ls%04d_stream%s_%s%s" % (
                    run,
                    play_lumi,
                    stream,
                    stream_source,
                    dat_orig_ext,
                )

                # read the original file name, for copying
                with open(input_join(jsn_orig_fn), "r") as f:
                    jsn_data = json.load(f)
                    dat_orig_fn = jsn_data["data"][3]

                # copy the data file
                if os.path.exists(input_join(dat_orig_fn)):
                    self.make_copy(input_join(dat_orig_fn), output_join(dat_play_fn))

                    written_files.add(output_join(dat_play_fn))
                else:
                    log.warning("Dat file is missing: %s", dat_orig_fn)

                # write a new json file point to a different data file
                # this has to be atomic!
                jsn_data["data"][3] = dat_play_fn
                new_jsn_data = json.dumps(jsn_data)
                atomic_write(output_join(jsn_play_fn), new_jsn_data)

                written_files.add(output_join(jsn_play_fn))

            log.info("Copied %d files for lumi %06d", len(written_files), play_lumi)
        else:
            log.info(
                "Files for this lumi (%06d) will be skipped (to simulate holes in delivery)",
                play_lumi,
            )

        return  # files are rather removed gently by FileDeleter greenlet
        self.manager.register_files_for_cleanup(run, play_lumi, written_files)


class RunManager(object):
    """
    A helper class to manage multiple runs.

    This class handles deletion of the old runs, file cleanup, and run number allocation.
    """

    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.file_cleanup_backlog = []
        self.min_run_number = 100000

    def load_config(self):
        try:
            config_file = self.kwargs["opts"]["simulator.conf"]
            with open(config_file, "r") as f:
                self.config = json.load(f)
                return dict(self.config)
        except:
            log.error("Error reading the configuration file", exc_info=True)
            sys.exit(1)

    def find_run_number(self):
        # find an empty run number
        known_runs = [self.min_run_number, int(self.config["run"])]

        cl = os.path.join(self.config["ramdisk"], "current")
        if os.path.lexists(cl) and os.path.islink(cl):
            dest = os.readlink(cl)
            known_runs.append(int(dest.strip("run")))

        run_write_file = self.config.get("run_write_file", None)
        if run_write_file and os.path.exists(run_write_file):
            with open(run_write_file, "r") as fd:
                known_runs.append(int(fd.read().strip()))

        latest_run = max(known_runs) + 1
        log.info("Found next run number: %d", latest_run)
        return latest_run

    def manage_forever(self):
        """Starts new runs indefinetly...

        ... until run crashes or goes into 'error' state.

        In this case the error will be logged and the whole application will be restarted.
        """

        while True:
            self.load_config()

            # now make a config for a run
            config = dict(self.config)
            config["run"] = self.find_run_number()

            log.info("Preparing to run playback run %d", config["run"])
            r = SimulatorRun(self, config, self.kwargs)
            self.current_run = r
            r.run()

            if r.state == "error":
                return 1

    def delete_run_directory(self, run_directory):
        log.info("Deleting old run directory: %s", run_directory)
        shutil.rmtree(run_directory, ignore_errors=True)

        # delete global file as well
        r = os.path.dirname(run_directory)
        f = os.path.basename(run_directory)

        global_file = os.path.join(r, "." + f + ".global")
        if os.path.isfile(global_file):
            log.info("Deleting old global file: %s", global_file)
            os.unlink(global_file)

    def clean_run_directory(self, run_directory):
        log.info("Cleaning old run directory: %s", run_directory)
        lst = os.listdir(run_directory)

        ext_to_clean = [".dat", ".pb"]
        for f in lst:
            _q, ext = os.path.splitext(f)
            if ext in ext_to_clean:
                fp = os.path.join(run_directory, f)
                log.info("Deleting orphaned file: %s", fp)

    def on_start_cleanup(self):
        """Cleanup directory.

        Should be called before manage_forever().
        """

        config = self.load_config()
        directories_to_delete = []

        for f in os.listdir(config["ramdisk"]):
            if not f.startswith("run"):
                continue

            run_directory = os.path.join(config["ramdisk"], f)
            if not os.path.isdir(run_directory):
                continue

            run_number = f.strip(".run.global")
            if run_number.isdigit():
                run_number = int(run_number)
                if run_number > self.min_run_number:
                    self.min_run_number = run_number
            else:
                continue

            log.info("Found old run directory: %s", run_directory)

            # now find the status file (it's written by this application)
            status_file = os.path.join(run_directory, "status")
            if not os.path.exists(status_file):
                continue

            with open(status_file, "r") as fd:
                status = json.loads(fd.read())

            # lock keys should always match
            my_key = self.kwargs["lock_key"]
            their_key = status.get("socket_name", "")

            if my_key == their_key:
                self.clean_run_directory(run_directory)
                directories_to_delete.append(run_directory)
            else:
                log.info(
                    "Skipping old run directory: %s, state = %s, %s != %s",
                    run_directory,
                    status["state"],
                    my_key,
                    their_key,
                )

        directories_to_delete.sort()

        to_keep = int(config["number_of_runs_to_keep"])
        to_delete = directories_to_delete[:-to_keep]
        for rd in to_delete:
            self.delete_run_directory(rd)

    def register_files_for_cleanup(self, run, lumi, written_files):
        """
        Deletes files which are older than number_of_ls_to_keep config parameter.
        Be aware that this works across runs (and so does number_of_ls_to_keep parameter)!

        This is usually called from inside SimulatorRun class.
        """

        if self.config["number_of_ls_to_keep"] >= 0:
            self.file_cleanup_backlog.append(
                (
                    run,
                    lumi,
                    written_files,
                )
            )

            while len(self.file_cleanup_backlog) > self.config["number_of_ls_to_keep"]:
                old_run, old_lumi, files_to_delete = self.file_cleanup_backlog.pop(0)

                log.info(
                    "Deleting %d files for old run/lumi: %d/%d",
                    len(files_to_delete),
                    old_run,
                    old_lumi,
                )
                for f in files_to_delete:
                    if os.path.exists(f):
                        os.unlink(f)


import fff_dqmtools
import fff_control
import fff_cluster


class FFFSimulatorSocket(fff_control.Ctrl):
    """
    This is a proxy to access run manager from a control socket.

    fff_control.Ctrl already handles client connections,
    all we have to do override handle_line function.

    Run manager should be set via "manager" member.
    """

    def handle_line(self, line, write_f):
        # get current SimulatorRun object
        # and pass the command to it
        run = getattr(getattr(self, "manager", None), "current_run", None)
        if run is None:
            write_f("no active run\n")
            return

        run.control(line.strip(), write_f)


@fff_cluster.host_wrapper(allow=["dqmrubu-c2a06-03-01"])
@fff_dqmtools.fork_wrapper(__name__)
@fff_dqmtools.lock_wrapper
def __run__(**kwargs):
    global log
    log = kwargs["logger"]

    manager = RunManager(kwargs)
    manager.on_start_cleanup()

    gthread, ctrl = FFFSimulatorSocket.enable(
        log, kwargs["lock_key"], kwargs["lock_socket"]
    )
    ctrl.manager = manager

    return manager.manage_forever()
