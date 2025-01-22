import re
import fff_dqmtools
import fff_cluster
import applets.fff_deleter as fff_deleter

regexp_files = re.compile(
    r"^\./DQM_V0001_(?P<subsystem>.+)_R(0)*(?P<run>\d{6})(?P<leftover>\.(root|jsn|origin))(\.deleted){0,1}$"
)

regexp_folders = re.compile(r".+")


def get_sort_key(d: dict) -> tuple:
    return (int(d["run"]),)


def get_run_path(d: dict) -> str:
    return "."


@fff_cluster.host_wrapper(allow=["dqmfu-c2b01-45-01", "dqmfu-c2b02-45-01"])
@fff_dqmtools.fork_wrapper(__name__)
@fff_dqmtools.lock_wrapper
def __run__(opts, **kwargs):
    log = kwargs["logger"]

    # Cleanup for EvD
    service_upload_cleanup = fff_deleter.FileDeleter(
        top="/home/dqmdevlocal/output/upload/",
        app_tag="fff_deleter_upload_playback_fus",
        thresholds={
            "rename": 60,
            "delete": 80,
        },
        log=log,
        report_directory=opts["path"],
        fake=opts["deleter.fake"],
        regexp_files=regexp_files,
        regexp_folders=regexp_folders,
        get_sort_key=get_sort_key,
        get_run_path=get_run_path,
    )
    service_upload_cleanup.delay_seconds = 30
    service_upload_cleanup.run_greenlet()
