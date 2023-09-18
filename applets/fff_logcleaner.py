import os
import logging
import sys
import fnmatch, glob
import time

log = logging.getLogger(__name__)
MAX_SIZE = 1024 * 1024 * 1024  # max file size, 1gb
KEEP_SIZE = 16 * 1024 * 1024  # position we truncate to and start overwritting from, 16m
MATCH = "/var/log/hltd/pid/hlt_run*_pid*.log"


def collect_open():
    running = set()

    for dir in glob.glob("/proc/*/fd/"):
        try:
            for fd in os.listdir(dir):
                l = os.readlink(os.path.join(dir, fd))
                if fnmatch.fnmatch(l, MATCH):
                    running.add(l)
        except:
            # we might get permission denied
            pass

    return running


def truncate_simple(file):
    f = open(file, "rb+")
    f.seek(KEEP_SIZE)
    f.truncate()
    f.write("\n\n... file was truncated at this point ...\n\n")
    f.close()


def truncate_keepend(file):
    f = open(file, "rb+")

    # get the remainder
    f.seek(-KEEP_SIZE, 2)
    remainder = f.read()

    f.seek(KEEP_SIZE, 0)
    f.truncate()
    f.write("\n\n... file was truncated at this point ...\n\n")
    f.write(remainder)
    f.close()


def do_the_log_cleanup(fake=False, running_set=None):
    assert MAX_SIZE > (KEEP_SIZE * 3)

    files = glob.glob(MATCH)
    for file in files:
        st = os.stat(file)
        if st.st_size > MAX_SIZE:
            log.info(
                "File %s is too big, %d > %d, truncating."
                % (file, st.st_size, MAX_SIZE)
            )

            if fake:
                continue

            if (running_set is not None) and (file not in running_set):
                log.info("No known processes are using this file, keeping the end.")
                truncate_keepend(file)
            else:
                truncate_simple(file)

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

        time.sleep(150)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    if os.geteuid() != 0:
        exit("You need root permissions to run this.")

    if len(sys.argv) == 2:
        newsize = int(sys.argv[1])

        print(
            "Set MAX_SIZE (%.03f) to %.03f megabytes."
            % (
                float(MAX_SIZE) / 1024 / 1024,
                newsize,
            )
        )
        MAX_SIZE = newsize * 1024 * 1024

    running_set = collect_open()
    print(
        "Running with max_size=%.03f keep_size=%.03f"
        % (
            float(MAX_SIZE) / 1024 / 1024,
            float(KEEP_SIZE) / 1024 / 1024,
        )
    )
    do_the_log_cleanup(running_set=running_set)
