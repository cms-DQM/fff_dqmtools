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
import fnmatch

from collections import OrderedDict, namedtuple

import fff_dqmtools
import applets.fff_filemonitor as fff_filemonitor
import applets.fff_deleter as fff_deleter
import fff_cluster

from utils import cmssw_deploy

log = logging.getLogger(__name__)


def find_pull_requests(fp):
    pr = []
    for entry in os.listdir(fp):
        m = re.match("merge.(\d+).log", entry)
        if m is None:
            continue

        i = int(m.group(1))
        p = cmssw_deploy.MergeRequest(
            id=i, type="merge-topic", label=None, arg=str(i), log=None
        )
        dct = dict(p._asdict())
        dct["log"] = None

        mlog_fp = os.path.join(fp, entry)
        with open(mlog_fp, "r") as f:
            dct["log"] = f.read().strip()

        pr.append(dct)

    return pr


def collect_releases(top):
    for directory in os.listdir(top):
        fp = os.path.realpath(os.path.join(top, directory))
        if not os.path.isdir(fp):
            continue

        log_fp = os.path.join(fp, "make_release.log")
        if not os.path.exists(log_fp):
            continue

        log.info("Found release area: %s", directory)

        r = cmssw_deploy.ReleaseEntry(
            name=directory,
            path=fp,
            pull_requests=[],
            options={},
            build_time={},
            log=None,
        )

        r = r._replace(pull_requests=find_pull_requests(fp))
        r = r._replace(build_time=os.path.getmtime(log_fp))

        with open(log_fp, "r") as f:
            r = r._replace(log=f.read().strip())

        yield r


class Analyzer(object):
    def __init__(self, top, report_directory, app_tag):
        self.top = top
        self.report_directory = report_directory
        self.app_tag = app_tag
        self.hostname = socket.gethostname()

    def make_report(self, backlog=5):
        # only last 5 entries
        for entry in collect_releases(self.top):
            id = "dqm-release-%s" % (entry.name)

            doc = {
                "sequence": 0,
                "hostname": self.hostname,
                "tag": self.app_tag,
                "name": entry.name,
                "path": entry.path,
                "build_time": entry.build_time,
                "extra": {
                    "pull_requests": entry.pull_requests,
                    "options": entry.options,
                    "log": entry.log,
                },
                "pid": os.getpid(),
                "_id": id,
                "type": "dqm-release",
            }

            final_fp = os.path.join(self.report_directory, doc["_id"] + ".jsn")
            body = json.dumps(doc, indent=None)
            fff_filemonitor.atomic_create_write(final_fp, body)

            log.info("Made report file: %s", final_fp)

    def run_greenlet(self):
        while True:
            if os.path.isdir(self.report_directory):
                self.make_report()
            else:
                log.warning(
                    "Directory %s does not exists. Reports disabled.",
                    self.report_directory,
                )

            time.sleep(105)


@fff_cluster.host_wrapper(allow=[""])
@fff_dqmtools.fork_wrapper(__name__, uid="dqmdev", gid="dqmdev")
@fff_dqmtools.lock_wrapper
def __run__(opts, logger, **kwargs):
    global log
    log = logger

    s = Analyzer(
        top=opts[os.path.dirname(opts["cmssw_path_playback"])],
        app_tag=kwargs["name"],
        report_directory=opts["path"],
    )

    s.run_greenlet()
