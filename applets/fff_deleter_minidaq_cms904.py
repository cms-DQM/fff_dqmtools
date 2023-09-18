import fff_dqmtools
import applets.fff_deleter as fff_deleter
import fff_cluster
import logging


@fff_cluster.host_wrapper(allow=["kvm-s904-r-ip10-01"])
@fff_dqmtools.fork_wrapper(__name__)
@fff_dqmtools.lock_wrapper
def __run__(opts, **kwargs):
    log = kwargs["logger"]

    ramdisk = "/cmsnfsdqmminidaq/dqmminidaq/"
    tag = "fff_deleter_minidaq_cms904"

    service = fff_deleter.FileDeleter(
        top=ramdisk,
        app_tag=tag,
        thresholds={
            "rename": 60,
            "delete": 80,
            "delete_folders": True,
        },
        log=log,
        report_directory=opts["path"],
        fake=opts["deleter.fake"],
    )
    service.delay_seconds = 15 * 60
    service.run_greenlet()
