import re
import fff_dqmtools
import fff_cluster
import applets.fff_deleter as fff_deleter

regexp_files = re.compile(
    r"^run(?P<run>\d+)/streamEvDOutput(2)?/run(?P<runf>\d+)_ls(?P<ls>\d+)(?P<leftover>_.+\.(root|jsn))(\.deleted){0,1}$"
)


@fff_cluster.host_wrapper(allow=["dqmrubu-c2a06-03-01"])
@fff_dqmtools.fork_wrapper(__name__)
@fff_dqmtools.lock_wrapper
def __run__(opts, **kwargs):
    log = kwargs["logger"]

    # Cleanup for EvD
    service_evd_output = fff_deleter.FileDeleter(
        top="/fff/output/EvD/",
        app_tag="fff_deleter_evd_playback_c2a06_03_01",
        thresholds={
            "rename": 60,
            "delete": 80,
        },
        log=log,
        report_directory=opts["path"],
        fake=opts["deleter.fake"],
        regexp_files=regexp_files,
    )
    service_evd_output.delay_seconds = 30
    service_evd_output.run_greenlet()
