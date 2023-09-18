import collections
import subprocess
import logging
import fnmatch
import pickle
import socket
import time
import json
import sys
import os
import re

from collections import namedtuple


# 'special' logger
class BufferedHandler(logging.StreamHandler):
    def __init__(self):
        logging.StreamHandler.__init__(self)

        self.backlog = collections.deque()
        self.backlog_size = 0
        self.backlog_size_max = 64 * 1024 * 1024

        self.aux_file = None

    def write_line(self, line):
        if self.aux_file is None:
            self.backlog.append(line)
            self.backlog_size += len(line)

            while self.backlog_size > self.backlog_size_max:
                x = self.backlog.popleft()
                self.backlog_size -= len(x)
        else:
            self.aux_file.write(line)

    def use_file(self, fn):
        if self.aux_file is not None:
            self.aux_file.close()

        self.aux_file = open(fn, "a")
        while self.backlog:
            x = self.backlog.popleft()
            self.backlog_size -= len(x)

            self.aux_file.write(x)

        self.aux_file.flush()

    def emit(self, record):
        logging.StreamHandler.emit(self, record)
        self.write_line(self.format(record) + "\n")

    def flush(self):
        logging.StreamHandler.flush(self)

        if self.aux_file is not None:
            self.aux_file.flush()


log = logging.getLogger(__name__)

MergeRequest = namedtuple("MergeRequest", ["id", "type", "label", "arg", "log"])
ScramProject = namedtuple(
    "ScramProject", ["project", "title", "tag", "arch", "path", "mtime"]
)
Commit = namedtuple("Commit", ["hash", "title"])
ReleaseEntry = namedtuple(
    "ReleaseEntry", ["name", "path", "pull_requests", "options", "build_time", "log"]
)

CacheFile = "~/.cmssw_deploy.pkl"
UserConfigFile = "~/.cmssw_deploy.jsn"

log_shell_re = re.compile("^\[(\d)\]\s")


def log_shell(line):
    level = 0
    line = line.decode("utf-8")
    match = log_shell_re.match(line)
    if match:
        level = int(match.group(1)) + 1
        line = log_shell_re.sub("", line)

    log.info("[%d] %s" % (level, line))
    handler.flush()


def shell_cmd(cmd, callback=None, guard=False, merge_stderr=True, **kwargs):
    log.info("Exec: %s", " ".join(cmd))

    args = dict(kwargs)
    if merge_stderr:
        args["stderr"] = subprocess.STDOUT

    p = subprocess.Popen(cmd, bufsize=1, stdout=subprocess.PIPE, **args)

    for line in iter(p.stdout.readline, b""):
        r = False
        if callback:
            r = callback(line)

        if r is True:
            pass
        else:
            # nice log
            log_shell(line.rstrip())

    ret = p.wait()

    if ret != 0:
        log.warning("Return code: %s", ret)

        if guard:
            raise Exception("Command failed with return code: %s" % ret)

    return ret


def check_if_hash(x):
    if len(x) < 5:
        return False

    try:
        int(x.lower(), base=16)
        return True
    except:
        return False


class ScramCache(object):
    """Manager global git/scram information,
    as well as configuration parameters.
    """

    def __init__(self):
        self.scram_projects = []
        self.scan_time = None

    def save(self):
        fp = os.path.expanduser(CacheFile)
        with open(fp, "w") as f:
            pickle.dump(self, f)

        log.info("Save pull request and release info to: %s", fp)

    @classmethod
    def load(cls):
        obj = cls()
        try:
            fp = os.path.expanduser(CacheFile)
            with open(fp, "r") as f:
                o = pickle.load(f)
                obj.scram_projects = o.scram_projects
                obj.scan_time = o.scan_time

        except:
            log.warning("Failed to open user cache.", exc_info=True)

        log.info("Loaded %d scram releases", len(obj.scram_projects))
        return obj

    def update(self):
        self.update_scram_stuff()
        self.scan_time = time.time()

    def update_scram_stuff(self):
        # arch_re = re.compile(r"slc\d_amd64_gcc[0-9]+")
        arch_re = re.compile(r"el\d_amd64_gcc[0-9]+")

        projects = []

        def parse_scram(line):
            s = line.strip().split()
            if len(s) != 3:
                return False

            project, tag, path = s
            project = project.decode("utf-8")
            tag = tag.decode("utf-8")
            path = path.decode("utf-8")
            arch = None

            m = arch_re.search(path)
            if m:
                arch = m.group(0)

            try:
                mtime = os.stat(path).st_mtime
            except:
                log.warning("Failed to get mtime for path: %s", path)
                mtime = None

            p = ScramProject(
                project=project, title=tag, tag=tag, arch=arch, path=path, mtime=mtime
            )
            projects.append(p)
            return True

        log.info("Updating scram info")
        shell_cmd(["scram", "l", "--all", "-c", "CMSSW"], callback=parse_scram)
        log.info("Found %d scram releases", len(projects))
        self.scram_projects = projects


def select_target(available, tag, arch, tag_blacklist):
    import readline

    readline.parse_and_bind("tab: complete")
    readline.set_completer_delims(" ")

    # we need to find one with multiple architectures
    # and display them with different titles (even though they are the same)
    tag_counts = {}
    for x in available:
        tag_counts[x.tag] = tag_counts.get(x.tag, 0) + 1

    anew = []
    for x in available:
        if tag_counts[x.tag] > 1:
            anew.append(x._replace(title=x.tag + ":" + x.arch))
        else:
            anew.append(x)

    anew.sort(key=lambda x: x.mtime, reverse=True)
    available = anew

    while True:
        # check for an exact match, special case
        x = list(filter(lambda x: tag == x.title, available))
        if len(x) == 1:
            return x[0]

        filtered = list(filter(lambda x: tag in x.title, available))

        completer_lst = []

        def completer_func(text, state):
            if state == 0:
                completer_lst[:] = filter(lambda x: x.title.startswith(text), filtered)

            if state < len(completer_lst):
                return completer_lst[state].title
            else:
                return None

        # print the choices
        if tag:
            log.warning("Filter: *%s*" % tag)
        readline.set_completer(completer_func)
        line = input("Tag to use (tab to complete, %d entries): " % len(filtered))
        readline.set_completer(None)
        line = line.strip()
        tag = line


def get_list_of_pr(string):
    # validate pull requests
    pull_requests = []
    if string:
        for p in string.split(","):
            i, t = int(p), "merge-topic"
            if p.startswith("+"):
                t = "cherry-pick"

            m = MergeRequest(i, t, label="%d" % i, arg=p, log=None)
            pull_requests.append(m)

    return pull_requests


def parse_commits(diff):
    commits = []

    def parse_commit(line):
        line = line.strip()
        h = line.partition(" ")
        c = Commit(hash=h[0], title=h[2])
        commits.append(c)

        return True

    shell_cmd(["git", "log", "--pretty=oneline", diff], callback=parse_commit)
    return commits


def parse_rev(key):
    hashes = []

    def parse_commit(line):
        line = line.strip()
        if line and check_if_hash(line):
            hashes.append(line)
            return True
        return False

    shell_cmd(["git", "rev-parse", key], callback=parse_commit)
    return hashes


def get_commits(mr):
    pr_head = "refs/gh-remotes/pull/%d/head" % mr.id
    return parse_commits("HEAD..%s" % pr_head), "HEAD", pr_head


def get_commits_vs_base(mr):
    pr_head = "refs/gh-remotes/pull/%d/head" % mr.id
    pr_head_rev = parse_rev(pr_head)[0]
    pr_merge = "refs/gh-remotes/pull/%d/merge" % mr.id

    parents = []

    def parse_commit(line):
        x = line.strip().split()
        if x:
            parents[:] = x
        return True

    shell_cmd(["git", "show", "--pretty=%P", "%s" % pr_merge], callback=parse_commit)

    parents = set(parents)
    parents.remove(pr_head_rev)

    if len(parents) != 1:
        raise Exception("Too many parents.")

    parent = parents.pop()
    return parse_commits("%s..%s" % (parent, pr_head)), parent, pr_head


def compare_commit_lists(list1, list2):
    list1 = set(list1)
    list2 = set(list2)

    d1, d2 = list1.difference(list2), list2.difference(list1)
    for d in d1:
        log.warning("Diff :-: %s", d)

    for d in d2:
        log.warning("Diff :+: %s", d)

    return (len(d1) + len(d2)) == 0


def apply_actual_pr(mr, args):
    # fetch the pr refs
    shell_cmd(
        [
            "git",
            "fetch",
            "official-cmssw",
            "refs/pull/%s/*:refs/gh-remotes/pull/%s/*"
            % (
                mr.id,
                mr.id,
            ),
        ],
        guard=True,
    )

    # check if this pr "compatible" with the branch we are on
    # basically, this checks if commits which would be applied during merging are
    # the same as the ones seen in github
    log.info("Merging: %s", mr)
    log.info("Checking difference (vs head)")
    c1 = get_commits(mr)
    # @TODO this is temporary disabled - github does not provide a way to consistently get parent id
    # log.info("Checking difference (vs parent)")
    # c2 = get_commits_vs_base(mr)
    r = compare_commit_lists([], c1[0])

    if mr.type == "merge-topic":
        # do the sparse checkout magic
        # shell_cmd(["git", "cms-sparse-checkout", c1[1], c1[2]], guard=True)
        # shell_cmd(["git", "read-tree", "-mu", "HEAD"], guard=True)

        log.info("'%s' is a merge-topic, will be done via git cms-merge-topic." % mr.id)
        shell_cmd(["git", "cms-merge-topic", "--ssh", str(mr.id)], guard=True)
    elif mr.type == "cherry-pick":
        raise Exception("Not yet implemented")

    if not args.no_build:
        shell_cmd(["git", "cms-checkdeps", "-a"], guard=True)
        shell_cmd(["scram", "b", "-j16"], guard=True)

    log.info("Merge successful: %s", mr)


def apply_pr(args):
    # some verification checks:
    list_os_mr = get_list_of_pr(args.pull_requests)
    if len(list_os_mr) != 1:
        raise Exception("Only one pull request can be applied using apply-pr.")

    mr = list_os_mr[0]

    # we have to be in src and "git" command is in available
    if not os.environ.has_key("CMSSW_BASE"):
        raise Exception("Please do cmsenv before calling me.")

    base_path = os.environ["CMSSW_BASE"]
    base_src_path = os.path.join(base_path, "src")

    common_prefix = os.path.commonprefix([os.getcwd(), base_path])
    if common_prefix != base_path:
        raise Exception("You have to be inside project's src directory.")

    os.chdir(base_src_path)

    # check for staged changes, abort if any (for safety)
    failed = []

    def fail_on_change(x):
        x = x.strip()
        if len(x) and x[0] in "ACDMRU":
            failed.append(x)
            log.error("Staged change: %s", x)
        return True

    shell_cmd(
        ["git", "status", "--porcelain", "--untracked=no"], callback=fail_on_change
    )

    if failed:
        raise Exception("Staged, but not commited change was found, aborting.")

    try:
        if mr.type in ("merge-topic", "cherry-pick"):
            apply_actual_pr(mr, args)
            pass
    except:
        log.error("Merge of %s has failed", mr, exc_info=True)
        sys.exit(1)


def make_src_backup(base_path, label):
    # prepare backup of src
    base_src_path = os.path.join(base_path, "src")

    backup_src_path = None
    backup_restore_commands = []
    backup_erase_commands = []

    # find the backup directory
    for i in range(1, 1024):
        b = os.path.join(base_path, "src." + label + ".%d" % i)
        if not os.path.exists(b):
            backup_src_path = b
            break

    if backup_src_path:
        log.info("Making a backup at: %s", backup_src_path)
        r = shell_cmd(
            [
                "rsync",
                "-ap",
                base_src_path + "/",
                backup_src_path + "/",
            ],
            guard=True,
        )
        if r == 0:
            backup_restore_commands.append(
                [
                    "rsync",
                    "-ap",
                    "--delete",
                    backup_src_path + "/",
                    base_src_path + "/",
                ]
            )
            backup_erase_commands.append(["rm", "-fr", backup_src_path])
            log.info("Backup successful: %s", backup_src_path)

            log.info("Do this to restore:")
            for cmds in backup_restore_commands + backup_erase_commands:
                log.info('  "%s"', " ".join(cmds))

    return backup_src_path, backup_restore_commands, backup_erase_commands


def apply_multiple_pr(base_path, args, cmd_prefix=[]):
    """wrapper to apply multiple pr, it will launch subprocesses for each pr"""

    list_os_mr = get_list_of_pr(args.pull_requests)
    status = {}

    script = os.path.abspath(sys.argv[0])

    for mr in list_os_mr:
        log.info("")
        log.info("")
        log.info("")
        log.info("Start *****merging***** new PR: %s", mr)

        # prepare args and env
        argv = cmd_prefix + [
            "python",
            script,
        ]
        if args.no_build:
            argv += ["--no-build"]
        argv += ["apply-pr", "-p", mr.arg]

        # prepare backup of src
        base_src_path = os.path.join(base_path, "src")
        backup_label = "backup_pre" + str(mr.label)
        backup_src_path, backup_restore_cmds, backup_erase_cmds = make_src_backup(
            base_path, backup_label
        )

        # create log file
        merge_log_file = os.path.join(base_path, "merge." + str(mr.label) + ".log")
        log.info("Logging into: %s", merge_log_file)

        merge_log_file_rel = os.path.relpath(merge_log_file, base_src_path)
        argv += ["--log", merge_log_file_rel]

        r = shell_cmd(argv, cwd=base_src_path, merge_stderr=True)
        status[mr] = (r, merge_log_file)

        # restore backup on failure
        if r == 0:
            # pr successful
            if (not args.no_restore) and backup_src_path:
                log.info("Erasing backup directory: %s", backup_src_path)
                for cmd in backup_erase_cmds:
                    shell_cmd(cmd, guard=True)

        else:
            # pr failed
            if (not args.no_restore) and backup_src_path:
                log.info("Restoring old directory")
                for cmd in backup_restore_cmds + backup_erase_cmds:
                    shell_cmd(cmd, guard=True)
                log.info('Done, it\'s probably a good idea to do "cd; cd -"')
            else:
                break

    log.warning("Applied merge requests (%d):", len(status))
    for mr in status.keys():
        if status[mr][0] == 0:
            log.info("  %s: success", mr)
        else:
            log.info("  %s: failed", mr)
            log.info("  See: %s", status[mr][1])


def make_release(sc, args):
    # go to a specified directory, if specified
    # if args.path != "./":
    #    log.info("Switching to directory: %s", args.path)
    #    os.chdir(args.path)

    # find the release to use
    target = select_target(sc.scram_projects, args.tag, args.arch, args.tag_blacklist)
    log.info("Selected release: %s %s", target.arch, target.tag)

    log.info("Make release invoked on: %s", socket.gethostname())
    log.info("Make release args: %s", args)
    log.info("Make release cmdline: %s", " ".join(sys.argv))
    # log.info("Make release env: %s", os.environ)

    # generate the directory_name
    components = [target.tag]
    if args.label:
        components = [
            args.label,
            "_",
        ] + components

    pull_requests = get_list_of_pr(args.pull_requests)
    if len(pull_requests) <= 5:
        for m in pull_requests:
            components.append("_" + m.label)
    else:
        for m in pull_requests[:3]:
            components.append("_" + m.label)

        components.append("___" + pull_requests[-1].label)

    name = "".join(components)
    base_cwd = args.path
    base_path = os.path.join(base_cwd, name)

    log.info("Generated directory name: %s", base_path)

    # check if directory exists
    if os.path.exists(base_path):
        log.error("Directory path (%s) already exists, delete it.", name)
        sys.exit(1)

    # create a scram project there
    if os.environ.get("SCRAM_ARCH", "x") != target.arch:
        log.info("Setting SCRAM_ARCH=%s", target.arch)
        os.environ["SCRAM_ARCH"] = target.arch

    if args.use_tmp:
        base_final_cwd = base_cwd
        base_final_path = base_path

        import tempfile

        tmp_dir = tempfile.mkdtemp(prefix="cmssw_scram_deploy")
        assert tmp_dir
        assert "scram" in tmp_dir

        base_cwd = tmp_dir
        base_path = os.path.join(base_cwd, name)
        tmp_to_delete = [tmp_dir]

        log.info("Doing everything inside %s directory.", tmp_dir)

    shell_cmd(["scram", "p", "-n", name, target.tag], cwd=base_cwd, guard=True)

    # create a special "wrapper" file, to help us execute commands
    # inside the cmssw environment
    with open(os.path.join(base_path, "cmswrapper.sh"), "w") as f:
        os.fchmod(f.fileno(), 0o755)
        f.write("#!/bin/sh\n")
        f.write("")
        f.write("# cmdline: %s\n" % " ".join(sys.argv))
        f.write("eval `scramv1 runtime -sh`\n")
        f.write("")
        f.write('exec "$@"\n')

    # do git init
    log.info("Doing git init (this takes time)")
    base_src_path = os.path.join(base_path, "src")
    shell_cmd(["./cmswrapper.sh", "git-cms-init", "--ssh"], cwd=base_path, guard=True)
    shell_cmd(
        ["../cmswrapper.sh", "git", "tag", "cmssw_manager_base"], cwd=base_src_path
    )

    if args.pull_requests:
        log.info("Applying pr string: %s", args.pull_requests)
        cmd_prefix = ["../cmswrapper.sh"]
        apply_multiple_pr(base_path, args, cmd_prefix=cmd_prefix)

    if args.use_tmp:
        # shell_cmd(["rsync", "-ap", "--no-g", base_path + "/", base_final_path + "/"], guard=True)
        shell_cmd(["rsync", "-ap", base_path + "/", base_final_path + "/"], guard=True)
        shell_cmd(["scram", "b", "ProjectRename"], guard=True, cwd=base_final_path)

        # switch back the path
        base_cwd = base_final_cwd
        base_path = base_final_path

        for tmp in tmp_to_delete:
            shell_cmd(["rm", "-fr", tmp], guard=True)

    log.warning("Made release area: %s", name)

    if handler.aux_file is None:
        log_file = os.path.join(base_path, "make_release.log")
        log.warning("Saving log file to: %s", log_file)
        handler.use_file(log_file)


class UserConfig(object):
    """Manager user configuration parameters."""

    def __init__(self):
        self.fp = os.path.expanduser(UserConfigFile)
        self.store = {}

    def save(self):
        with open(self.fp, "w") as f:
            json.dump(self.store, f, indent=2)

    def load(self):
        try:
            with open(self.fp, "r") as f:
                self.store = json.load(f)
        except:
            log.warning("Failed to open user config.", exc_info=True)

    def get_config(self, key, default=None):
        return self.store.get(key, default)

    def update_config(self, key, value):
        if self.store.get(key) == value:
            return

        log.info("Setting key %s to: %s", key, value)
        self.store[key] = value


def parse_args():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Increase output verbosity"
    )
    parser.add_argument(
        "command",
        help="What to do: update,make-release,select-release,apply-pr,apply-multiple-pr,build",
    )

    group = parser.add_argument_group("global_info", "Global information")
    group.add_argument(
        "--repo",
        type=str,
        default="git@github.com:cms-sw/cmssw.git",
        help="Main git repository.",
    )
    group.add_argument("--path", type=str, default="./", help="Default path.")
    group.add_argument(
        "--log", type=str, help="Log file to append (does not quiet stdout)"
    )

    group = parser.add_argument_group("release_info", "Release information")
    group.add_argument(
        "-t",
        "--tag",
        type=str,
        default="",
        help="Main release tag to use as a base (this is scram tag, not git's.",
    )
    group.add_argument(
        "-b",
        "--tag-blacklist",
        type=str,
        default="ROOT5,THREADED,ICC,CLANG,DEVEL",
        help="Blacklist words in scram tags (they don't appear in auto-select).",
    )
    group.add_argument(
        "-a",
        "--arch",
        type=str,
        default="",
        help="Scram arch, don't set for auto-select.",
    )
    group.add_argument(
        "-p",
        "--pull-requests",
        type=str,
        default="",
        help='Comma-separate list of pull requests to apply. Use "+number" to merge using cherry pick instead of merge-topic.',
    )
    group.add_argument(
        "-l",
        "--label",
        type=str,
        default="",
        help="Label to prefix the directory with.",
    )
    group.add_argument(
        "--use-tmp",
        action="store_true",
        help="Do scram stuff in tmp (work around against jira #8215",
    )

    group = parser.add_argument_group("merge_info", "Merging options")
    group.add_argument(
        "--no-backup",
        action="store_true",
        help="Don't make backup of the src dir before merging.",
    )
    group.add_argument(
        "--no-restore",
        action="store_true",
        help="Restore from backup in case of a merge failure.",
    )
    group.add_argument("--no-build", action="store_true", help="Don't call scram b.")

    args = parser.parse_args()
    return args


if __name__ == "__main__":
    log.setLevel(logging.INFO)
    handler = BufferedHandler()
    log.addHandler(handler)

    args = parse_args()

    # init or load git/scram cache
    scram_cache = ScramCache()
    scram_cache.update()

    if args.log:
        handler.use_file(args.log)

    command = args.command
    if command == "update":
        pass
    elif command == "select-release":
        target = select_target(
            scram_cache.scram_projects, args.tag, args.arch, args.tag_blacklist
        )
        print(target)
    elif command == "make-release":
        make_release(scram_cache, args)
    # elif command == "prepare-online-release":
    #    make_release(scram_cache, args)
    elif command == "apply-pr":
        apply_pr(args)
    elif command == "apply-multiple-pr":
        if not os.environ.has_key("CMSSW_BASE"):
            raise Exception("Please do cmsenv before calling me.")

        base_path = os.environ["CMSSW_BASE"]
        apply_multiple_pr(base_path, args)
    else:
        log.error("Unknown command: %s", command)
