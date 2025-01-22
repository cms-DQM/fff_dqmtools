import fff_dqmtools
import fff_cluster
import applets.fff_deleter as fff_deleter
import logging


@fff_cluster.host_wrapper(allow=["dqmrubu-c2a06-03-01"])
@fff_dqmtools.fork_wrapper(__name__)
@fff_dqmtools.lock_wrapper
def __run__(opts, **kwargs):
    log = kwargs["logger"]

    service_ramdisk = fff_deleter.FileDeleter(
        top="/fff/ramdisk/",
        app_tag="fff_deleter_playback_c2a06_03_01",
        thresholds={
            "rename": 60,
            "delete": 80,
        },
        log=log,
        report_directory=opts["path"],
        fake=opts["deleter.fake"],
    )
    service_ramdisk.delay_seconds = 30

    service_ramdisk.run_greenlet()
