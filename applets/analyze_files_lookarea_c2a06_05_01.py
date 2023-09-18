import fff_dqmtools
import fff_cluster
import logging


@fff_cluster.host_wrapper(allow=["dqmrubu-c2a06-05-01"])
@fff_dqmtools.fork_wrapper(__name__, uid="dqmpro", gid="dqmpro")
@fff_dqmtools.lock_wrapper
def __run__(opts, **kwargs):
    import analyze_files

    analyze_files.log = kwargs["logger"]

    s = analyze_files.Analyzer(
        top="/fff/output/lookarea/",
        app_tag=kwargs["name"],
        report_directory=opts["path"],
    )

    s.run_greenlet()
