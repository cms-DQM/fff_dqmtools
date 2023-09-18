import os
import logging
import sys
import fnmatch, glob
import time

log = logging.getLogger(__name__)

MATCH = (
    "/var/log/hltd/pid/hlt_run*_pid*_gzip.log.gz",
    "/var/log/hltd/pid/hlt_run*_pid*.log",
)
DELETE_HOURS = 14 * 24 * 1


def do_the_log_cleanup(fake=False, running_set=None):
    now = time.time()

    def age(fn):
        st = os.stat(fn)
        elapsed = now - st.st_mtime
        return float(elapsed) / 3600

    for match in MATCH:
        files = glob.glob(match)
        for file in files:
            hours = age(file)

            if hours > DELETE_HOURS:
                log.info("Deleting %s, age: %.02f hours.", file, hours)
                os.unlink(file)

    return len(files)


import fff_dqmtools


@fff_dqmtools.fork_wrapper(__name__)
@fff_dqmtools.lock_wrapper
def __run__(opts, **kwargs):
    global log
    log = kwargs["logger"]

    while True:
        try:
            start = time.time()
            files = do_the_log_cleanup()
            took = time.time() - start
            log.info(
                "Log cleaner finished, checked %d files, took %.02f seconds.",
                files,
                took,
            )
        except:
            log.warning("Log cleaner crashed!", exc_info=True)

        time.sleep(15 * 60)
