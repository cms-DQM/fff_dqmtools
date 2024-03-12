#!/usr/bin/env python3

import sys
import os
import logging
import re
import datetime
import subprocess
import socket
import time
import json
import shutil
from collections import OrderedDict, namedtuple

import fff_dqmtools
import applets.fff_filemonitor as fff_filemonitor
import fff_cluster

DataEntry = namedtuple("DataEntry", ["key", "path", "fsize", "ftime"])

re_files = re.compile(
    r"^run(?P<run>\d+)/(open\/){0,1}run(?P<runf>\d+)_ls(?P<ls>\d+)(?P<leftover>_.+\.(dat|raw|pb))(\.deleted){0,1}$"
)
re_folders = re.compile(r"^run(?P<run>\d+)$")


def parse_file_name(rl):
    m = re_files.match(rl)
    if not m:
        return None, None

    d = m.groupdict()
    sort_key = (int(d["run"]), int(d["runf"]), int(d["ls"]), d["leftover"])

    run_path = "run" + d["run"]
    return sort_key, run_path


def collect(top, log):
    # entry format for files: DataEntry
    collected = []

    # same as above, but per directory
    collected_paths = {}

    def stat(x):
        try:
            stat = os.stat(x)
            fsize = stat.st_size
            ftime = stat.st_ctime
            return fsize, ftime
        except:
            log.error("Failed to stat file or directory: %s", fp, exc_info=True)
            return 0, 0

    for root, dirs, files in os.walk(top, topdown=True):
        root_rl = os.path.relpath(root, top)

        # skip hidden stuff, usually ".snapshots"
        files = [f for f in files if not f.startswith(".")]
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        # don't recurse into "deleted" runs
        dirs[:] = [d for d in dirs if not (d.startswith("run") and ".deleted" in d)]

        # insert folder descriptions
        if re_folders.match(root_rl) is not None:
            if root_rl not in collected_paths:
                _dsize, dtime = stat(root)
                collected_paths[root_rl] = DataEntry(root_rl, root, 0, dtime)

        for name in files:
            fp = os.path.join(root, name)
            rl = os.path.join(root_rl, name)

            # rl is always root relative!
            sort_key, run_rl = parse_file_name(rl)
            if sort_key:
                fsize, ftime = stat(fp)

                collected.append(DataEntry(sort_key, fp, fsize, ftime))

                d = collected_paths[run_rl]
                collected_paths[run_rl] = d._replace(fsize=d.fsize + fsize)

    # for now just use simple sort
    collected.sort(key=lambda x: x[0])

    collected_paths = list(collected_paths.values())
    collected_paths.sort(key=lambda x: x[0])
    return collected, collected_paths


class FileDeleter(object):
    def __init__(
        self,
        top,
        thresholds,
        report_directory,
        log,
        fake=True,
        skip_latest=False,
        app_tag="fff_deleter",
    ):
        self.top = top
        self.fake = fake
        self.thresholds = thresholds
        self.report_directory = report_directory
        self.sequence = 0
        self.app_tag = app_tag
        self.log = log
        self.delay_seconds = 30
        self.skip_latest = skip_latest

        self.hostname = socket.gethostname()

        if self.fake:
            self.log.info("Starting in fake (read only) mode.")

    def rename(self, f):
        if f.endswith(".deleted"):
            return f

        fn = f + ".deleted"

        if self.fake:
            self.log.warning(
                "Renaming file (fake): %s -> %s",
                f,
                os.path.relpath(fn, os.path.dirname(f)),
            )
        else:
            self.log.warning(
                "Renaming file: %s -> %s", f, os.path.relpath(fn, os.path.dirname(f))
            )

            # try:
            os.rename(f, fn)
            # except:
            #    self.log.warning("Failed to rename file: %s", f, exc_info=True)

        return fn

    def overwrite(self, f):
        if not f.endswith(".deleted"):
            return f

        if self.fake:
            self.log.warning("Truncating file (fake): %s", f)
        else:
            self.log.warning("Truncating file: %s", f)

            try:
                open(f, "w").close()
            except:
                self.log.warning("Failed to truncate file: %s", f, exc_info=True)

        return f

    def delete(self, f, json=False):
        if not f.endswith(".deleted"):
            return f

        to_delete = [f]
        if json:
            jsn = f.split(".")[:-2] + ["jsn"]
            jsn = ".".join(jsn)
            to_delete.append(jsn)

        for ftd in to_delete:
            if self.fake:
                self.log.warning("Deleting file (fake): %s", ftd)
            else:
                self.log.warning("Deleting file: %s", ftd)

                try:
                    os.unlink(ftd)
                except:
                    self.log.warning("Failed to truncate file: %s", f, exc_info=True)

        return f

    def delete_folder(self, folder):
        if self.fake:
            self.log.warning("Deleting folder (fake): %s", folder)
        else:
            self.log.warning("Deleting folder: %s", folder)
            shutil.rmtree(folder)

        return folder

    def calculate_threshold(self, type_string):
        """Calculates how much bytes we have to delete
        in order to reach the threshold percentange.

        If the threshold is set to 80% and the disk
        at 89%, it fill return 9% in bytes.
        """

        threshold = self.thresholds[type_string]
        st = os.statvfs(self.top)
        total = st.f_blocks * st.f_frsize
        used = total - (st.f_bavail * st.f_frsize)
        stopSize = used - float(total * threshold) / 100

        self.log.info(
            "Using %d (%.02f%%) of %d space, %d (%.02f%%) above %s threshold.",
            used,
            float(used) * 100 / total,
            total,
            stopSize,
            float(stopSize) * 100 / total,
            type_string,
        )

        return stopSize

    def do_the_cleanup(self):
        self.sequence += 1
        if not os.path.isdir(self.top):
            self.log.warning("Directory %s does not exists.", self.top)
            return

        stopSizeRename = self.calculate_threshold("rename")
        stopSizeDelete = self.calculate_threshold("delete")

        assert stopSizeRename > stopSizeDelete

        # do the action until we reach the target sizd
        self.log.info("Started file collection at %s", self.top)
        start = time.time()
        collected, collected_paths = collect(self.top, self.log)
        self.log.info("Done file collection, took %.03fs.", time.time() - start)

        latest_run = collected[-1][0][0]

        file_count = len(collected)

        # stopSizeDelete can still be positive after this
        # meaning some files have to be deleted, but have not been (only marked)
        # these files will be deleted next iteration (30s.)

        start_cleanup = time.time()
        for entry in collected:
            sort_key, fp, fsize, ftime = entry
            if self.skip_latest and latest_run == sort_key:
                continue  # do not want to

            # unlink file and json older than 2 days
            # this has no effect on thresholds, but affects performance
            age = start - ftime
            if fsize == 0 and age >= 2 * 24 * 60 * 60 and fp.endswith(".deleted"):
                # remove empty and old files
                # no one uses them anymore...
                self.delete(fp, json=True)

            if stopSizeRename <= 0:
                break

            if fsize > 0:
                stopSizeRename -= fsize

                if fp.endswith(".deleted") and stopSizeDelete > 0:
                    # overwrite the files which have been previously marked with dummy
                    # and we have disk over-usage
                    stopSizeDelete -= fsize

                    self.overwrite(fp)
                elif fp.endswith(".deleted"):
                    # already renamed, do nothing
                    pass
                elif not fp.endswith(".deleted"):
                    # rename them, as a warning for the next iteration
                    self.rename(fp)

        if "delete_folders" in self.thresholds and self.thresholds["delete_folders"]:
            for entry in collected_paths:
                if self.skip_latest and str(latest_run) in entry.path:
                    continue

                # check if empty - we don't non-empty dirs
                # empty as in a 'no stream files left to truncate' sense
                if entry.fsize != 0:
                    continue

                # check if older than 7 days
                age = start - entry.ftime
                if age <= 7 * 24 * 60 * 60:
                    continue

                self.delete_folder(entry.path)

        self.log.info("Done cleanup, took %.03fs.", time.time() - start_cleanup)
        return file_count

    def make_report(self, file_count):
        if not os.path.isdir(self.report_directory):
            self.log.warning(
                "Directory %s does not exists. Reports disabled.", self.report_directory
            )
            return

        # calculate the disk usage
        if os.path.isdir(self.top):
            st = os.statvfs(self.top)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - free
        else:
            used, free, total = -1, -1, -1

        doc = {
            "sequence": self.sequence,
            "disk_used": used,
            "disk_free": free,
            "disk_total": total,
            "hostname": self.hostname,
            "tag": self.app_tag,
            "extra": {
                "file_count": file_count,
                "thresholds": self.thresholds,
            },
            "pid": os.getpid(),
            "_id": "dqm-diskspace-%s-%s"
            % (
                self.hostname,
                self.app_tag,
            ),
            "type": "dqm-diskspace",
        }

        final_fp = os.path.join(self.report_directory, doc["_id"] + ".jsn")
        body = json.dumps(doc, indent=True)
        fff_filemonitor.atomic_create_write(final_fp, body)

        self.log.info("Made report file: %s", final_fp)

    def run_greenlet(self):
        import gevent

        while True:
            files = self.do_the_cleanup()

            self.make_report(files)
            gevent.sleep(self.delay_seconds)


## Applet code is no longer used, but serves as an example.
## Actual deleters applets should import this module

## @fff_cluster.host_wrapper(allow = ["bu-c2f13-31-01"])
## @fff_dqmtools.fork_wrapper(__name__)
## @fff_dqmtools.lock_wrapper
## def __run__(opts, **kwargs):
##     log = kwargs["logger"]
##
##     service = FileDeleter(
##         top = opts["deleter.ramdisk"],
##         app_tag = opts["deleter.tag"],
##         thresholds = {
##             'rename': 60,
##             'delete': 80,
##         },
##         log = log,
##         report_directory = opts["path"],
##         fake = opts["deleter.fake"],
##     )
##
##     service.run_greenlet()
