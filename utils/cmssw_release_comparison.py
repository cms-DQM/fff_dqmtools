import re
import sys
import logging
from shlex import split
from pathlib import Path
from shutil import rmtree, move
from subprocess import Popen
import multiprocessing as mp
from datetime import datetime
from argparse import ArgumentParser, Namespace

REGEX_CMSSW_RELEASE = (
    r"CMSSW_(?P<major>\d{1,2})_(?P<minor>\d{1,2})_(?P<patch>\d{1,2})(_(?P<other>.+))?"
)

# Just an innocent magic number.
MAGIC_NUMBER = 69420

DQM_MACHINES = []

BASE_PATH_DQM_CLIENTS = Path("DQM", "Integration", "python", "clients")

DQM_CLIENTS = [
    # "beam_dqm_sourceclient-live_cfg.py",  # Requires db.key
    # "beamhlt_dqm_sourceclient-live_cfg.py",  # Requires db.key
    "beampixel_dqm_sourceclient-live_cfg.py",
    # "beamspotdip_dqm_sourceclient-live_cfg.py",  # Requires to be run in P5 to access cmsdimns{1,2}.cern.ch
    "csc_dqm_sourceclient-live_cfg.py",
    "ctpps_dqm_sourceclient-live_cfg.py",
    "dt4ml_dqm_sourceclient-live_cfg.py",
    "dt_dqm_sourceclient-live_cfg.py",
    "ecalcalib_dqm_sourceclient-live_cfg.py",
    "ecal_dqm_sourceclient-live_cfg.py",
    "ecalgpu_dqm_sourceclient-live_cfg.py",
    "es_dqm_sourceclient-live_cfg.py",
    "fed_dqm_sourceclient-live_cfg.py",
    "gem_dqm_sourceclient-live_cfg.py",
    "hcalcalib_dqm_sourceclient-live_cfg.py",
    "hcal_dqm_sourceclient-live_cfg.py",
    "hcalgpu_dqm_sourceclient-live_cfg.py",
    "hcalreco_dqm_sourceclient-live_cfg.py",
    "hlt_dqm_clientPB-live_cfg.py",
    "hlt_dqm_sourceclient-live_cfg.py",
    "info_dqm_sourceclient-live_cfg.py",
    "l1tstage2_dqm_sourceclient-live_cfg.py",
    "l1tstage2emulator_dqm_sourceclient-live_cfg.py",
    "mutracking_dqm_sourceclient-live_cfg.py",
    "onlinebeammonitor_dqm_sourceclient-live_cfg.py",
    "pfgpu_dqm_sourceclient-live_cfg.py",
    "pixel_dqm_sourceclient-live_cfg.py",
    "pixelgpu_dqm_sourceclient-live_cfg.py",
    "pixellumi_dqm_sourceclient-live_cfg.py",
    "ppsrandom_dqm_sourceclient-live_cfg.py",
    "rpc_dqm_sourceclient-live_cfg.py",
    "sistrip_dqm_sourceclient-live_cfg.py",
    "sistrip_approx_dqm_sourceclient-live_cfg.py",
    # "visualization-live_cfg.py",  # TODO find if any ROOT file is created
    # "visualization-live-secondInstance_cfg.py",  # TODO find if any ROOT file is created
]


def configure_logging(verbose: bool = False):
    logging.basicConfig(
        format="%(asctime)s - %(levelname)-8s - %(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
    )


def parse_args() -> Namespace:

    parser = ArgumentParser(
        description="""Script which creates two CMSSW releases (a base one and a comparison one), each one
optionally augmented with PRs, in the directory pointed by --working-dir, then runs the DQM clients on the
specified input streamers, compares the outputs and generates ROOT files with the differences, as well
as an HTML report with links to the target DQMGUI.

Mainly targeted for use in P5 machines.
"""
    )
    parser.add_argument(
        "base_release",
        type=str,
        help="The exact CMSSW release name to compare against",
    )
    parser.add_argument(
        "comparison_release",
        type=str,
        help="The exact CMSSW release name to compare against base_release",
    )
    parser.add_argument(
        "-i",
        "--input-dir",
        type=Path,
        required=True,
        help="Full path to directory where the input streams are (.dat and .jsn stream files)",
    )
    parser.add_argument(
        "-k",
        "--run-key",
        type=str,
        required=True,
        choices=["pp_run", "hi_run", "cosmic_run"],
        help="Run key of the run pointed to by input-dir",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Increase output verbosity"
    )
    parser.add_argument(
        "--pull-requests-base",
        type=str,
        required=False,
        help="Comma-separated list of pull requests to apply to the base release. "
        'Use "+number" to merge using cherry pick instead of merge-topic.',
    )
    parser.add_argument(
        "--pull-requests-comp",
        type=str,
        required=False,
        help="Comma-separated list of pull requests to apply to the comparison release. "
        'Use "+number" to merge using cherry pick instead of merge-topic.',
    )
    parser.add_argument(
        "-c",
        "--cmssw-deploy-path",
        type=Path,
        help="Full path to cmssw_deploy.py path. Default: %(default)s",
        required=False,
        default="/opt/fff_dqmtools/utils/cmssw_deploy.py",
    )
    parser.add_argument(
        "-w",
        "--working-dir",
        type=Path,
        help="Full path to directory where the CMSSW releases will be created in. Default: %(default)s",
        required=False,
        default="/dqmdata/dqm_cmssw/release_comparison",
    )

    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=False,
        default="/nfshome0/dqmdev/cmssw_comparison_results",
        help="Full path to directory where the comparison results will be stored in. Default: %(default)s",
    )
    parser.add_argument(
        "--gt-hlt-base",
        type=str,
        required=False,
        help="HLT Global Tag to use for the base release",
    )
    parser.add_argument(
        "--gt-express-base",
        type=str,
        required=False,
        help="Express Global Tag to use for the base release",
    )
    parser.add_argument(
        "--gt-hlt-comp",
        type=str,
        required=False,
        help="HLT Global Tag to use for the comparison release",
    )
    parser.add_argument(
        "--gt-express-comp",
        type=str,
        required=False,
        help="Express Global Tag to use for the comparison release",
    )
    parser.add_argument(
        "--reuse-releases",
        action="store_true",
        help="Reuse the base or comparison releases if a release with the same name was created before in the working-dir."
        + " By default, if a release exists when the script runs, it is removed and re-created.",
    )
    parser.add_argument(
        "--num-jobs",
        type=int,
        default=mp.cpu_count(),
        help="Number of jobs to run in parallel. Default: %(default)s",
    )
    parser.add_argument(
        "--dont-run-clients",
        action="store_true",
        help="Don't run the DQM clients for each release.",
    )
    parser.add_argument(
        "--dqmgui-url",
        type=str,
        default="http://dqmsrv-c2a06-08-01.cms:8030/dqm/online-playback",
        help="DQMGUI url to upload the resulting ROOT files to. Default: %(default)s",
    )
    parser.add_argument(
        "--dqmgui-url-alias",
        type=str,
        help="Base DQMGUI url to replace the DQMGUI links in the comparison HTML with. Example: https://cmsweb.cern.ch/dqm/online-playback",
    )

    return parser.parse_args()


def create_release(
    release_name: str,
    cmssw_deploy_path: Path,
    working_dir: Path,
    label: str = "",
    pull_requests: str = "",
    gt_hlt: str = "",
    gt_express: str = "",
    build: bool = True,
    reuse_release: bool = False,
) -> Path:
    """
    Call cmssw_deploy.py to create a new CMSSW release.

    cmssw_deploy_path: The full path to cmssw_deploy.
    label: The identifier to give to the release.
    working_dir: the directory where cmssw_deploy will be called, i.e. where the release will be created.
    pull_requests: A string of comma-separated ints to be passed as-is to cmssw_deploy (e.g. "35542,+23331").
    build: Whether or not to build (scram b) the release.
    reuse_release: If true and a directory with the exact same name with the one that would be
        created is found, it's reused. Else, if it exists it is deleted first.

    """

    # The label of the release will be compare__MMDD
    release_label = (
        "comparison_" + (f"{label}_" if label else "") + datetime.now().strftime("%m%d")
    )
    # The directory name that cmssw_deploy will puth the release into.
    # Reconstructing it here to check whether it exists or not.
    release_dir = f"{release_label}_{release_name}" + (
        "_" + "_".join([pr.lstrip("+") for pr in pull_requests.split(",")])
        if pull_requests
        else ""
    )
    # Check whether a release with the same name exists
    release_dir_full_path = Path(working_dir, release_dir)
    if release_dir_full_path.exists():
        if reuse_release:
            logging.info(f"{release_dir_full_path} exists, reusing it.")
            return release_dir_full_path
        else:
            logging.info(f"{release_dir_full_path} exists, removing it.")
            rmtree(release_dir_full_path)
    # The cmssw_deploy.py command.
    cmd = (
        f"{sys.executable} {cmssw_deploy_path} make-release -l "
        + f"{release_label} -t {release_name}"
        + (" --no-build" if not build else "")
        + (f" -p  {pull_requests}" if pull_requests else "")
        + (f" --gt-hlt {gt_hlt}" if gt_hlt else "")
        + (f" --gt-express {gt_express}" if gt_express else "")
    )
    logging.info(f"Running: {cmd}.")
    p = Popen(split(cmd), cwd=working_dir, text=True)
    p.wait()
    if p.returncode:
        raise Exception(f"{cmd} returned code {p.returncode}")

    return release_dir_full_path


def preliminary_checks(args: Namespace):
    """
    Validate arguments
    """
    # Make sure PRs are numbers.
    if args.pull_requests_base:
        for pr in args.pull_requests_base.split(","):
            assert int(pr)
    if args.pull_requests_comp:
        for pr in args.pull_requests_comp.split(","):
            assert int(pr)

    # Make sure that the base release is a valid CMSSW one
    m = re.match(REGEX_CMSSW_RELEASE, args.base_release)
    if not m:
        raise Exception(
            f"{args.base_release} does not match regex {REGEX_CMSSW_RELEASE}"
        )
    m = re.match(REGEX_CMSSW_RELEASE, args.comparison_release)
    if not m:
        raise Exception(
            f"{args.base_release} does not match regex {REGEX_CMSSW_RELEASE}"
        )

    # Make sure that the cmssw_deploy.py path is correct
    assert args.cmssw_deploy_path.exists(), f"{args.cmssw_deploy_path} does not exist!"
    assert args.working_dir.exists(), f"{args.working_dir} does not exist!"
    assert args.input_dir.exists(), f"{args.input_dir} does not exist!"
    if args.output_dir and not args.output_dir.exists():
        logging.info(f"{args.output_dir} does not exist, creating it.")
        args.output_dir.mkdir(parents=True)
    assert re.match(r"run\d{6}", args.input_dir.name)


def run_single_client(
    release_path: Path,
    client: str,
    input_dir: Path,
    run_number: int,
    run_key: str,
    label: str,
) -> int:
    """
    Function that starts a single DQM client with the appropriate arguments.

    label is used to find the subdir in the logs dir to store execution logs.
    """
    client_path = release_path.joinpath("src", BASE_PATH_DQM_CLIENTS, client)
    logging.info(f"Starting client {client} ({client_path})")
    # outputBaseDir (where the ROOT files will be put) is left empty,
    # defaults to creating an "upload" directory inside the cwd.
    cmd = (
        f"{release_path.joinpath('cmswrapper.sh')} cmsRun {client_path}"
        + f" runkey={run_key} runInputDir={input_dir} runNumber={run_number} scanOnce=True"
        + " runUniqueKey=4e94e771-add6-41be-8683-X5f6a7a9ed1X"
    )
    log_path = release_path.joinpath("logs", label, f"{client}.log")
    logging.info(f"Running: {cmd}. Logs can be found in {log_path}")
    exit_code = 0
    with open(log_path, "w+") as f:
        p = Popen(split(cmd), cwd=release_path, stdout=f, stderr=f)
        exit_code = p.wait()

    if exit_code:
        logging.error(f"Client {client} returned exit code {exit_code}")
    else:
        logging.info(f"Client {client} exited gracefully")
    return exit_code


def run_clients(
    release_path: Path,
    input_dir: Path,
    run_number: int,
    run_key: str,
    num_jobs: int,
    label: str,
):
    logging.debug(f"Starting process pool of {num_jobs} processes")
    # Create upload dir if it does not already exist.
    if not release_path.joinpath("upload").exists():
        logging.info(f'Creating directory: {release_path.joinpath("upload")}')
        release_path.joinpath("upload").mkdir(parents=True)
    # Create the logs dir
    release_path.joinpath("logs", label).mkdir(exist_ok=True, parents=True)
    # Could not find a proper way to pass kwargs instead of args to
    # run_single_client. ProcessPoolExecutor was also tried,
    # but was getting stuck for no apparent reason, while ThreadPoolExecutor
    # was running fine with the exact same args (though we don't want it, as
    # it won't be truly parallel).
    # So, for now, args are passed as if they were positional.
    with mp.Pool(num_jobs) as p:
        exit_codes = p.starmap(
            run_single_client,
            (
                (release_path, client, input_dir, run_number, run_key, label)
                for client in sorted(DQM_CLIENTS)
            ),
        )
        # Let the pool know that no more swimmers are allowed
        p.close()
        # Wait for the processes to finish swimming in the pool
        p.join()

    failed_clients_index = [i for i, code in enumerate(exit_codes) if code != 0]
    if failed_clients_index:
        raise Exception(
            f"The following clients returned a non-zero exit code: {[sorted(DQM_CLIENTS)[i] for i in failed_clients_index]}"
        )


def rename_upload_dirs(release_path: Path, label: str) -> list:
    """
    The output directory of DQM clients is "./upload" relative to the
    working dir the process was executing from.
    We rename it because compareDQMOutput.py globs for files with the following
    filter: '*.*_*/DQM_*.root'. The directory is expected to be <workflow num>_<something>/.
    Workflow number is in the format of <int>.<int>

    We use a different workflow number (and directory) for each client dynamically,
    as it's the only way to differentiate between them, based on the way that
    compareHistograms.py (the script that will be called by compareDQMOutput.py)
    runs the comparison.

    Assumes that rename_output_root_files has already run.

    label is the str to use for a subdirectory to put the files into, to preserve
    per-execution results.

    Returns a list of client names, whose index corresponds to the dummy
    workflow id they have been assigned.
    """
    assert release_path.joinpath(
        "upload"
    ).is_dir(), f"{release_path.joinpath('upload')} was not found in {release_path}"
    workflow_dummy_id = 0
    workflow_to_client_map = []
    files = sorted(release_path.joinpath("upload").glob("*.root"))
    logging.debug(
        f"Found {len(files)} root files in {release_path.joinpath('upload')}."
    )
    for file in files:
        # file.name example: DQM_V0001_R000387878_Info.root
        # Create a dedicated upload dir for each client. It will look like "<MAGIC_NUMBER>.<num>_<client name>".
        # The "_<client name>" part means nothing as it's discarded later,
        # but is needed for complying with what compareDQMOutput.py expects.
        client_name = file.name.split("_")[-1].split(".")[0]
        new_directory = release_path.joinpath(label).joinpath(
            f"{MAGIC_NUMBER}.{workflow_dummy_id:02d}_{client_name}"
        )
        if new_directory.is_dir():
            rmtree(new_directory)
        logging.debug(f"Creating {new_directory}")
        new_directory.mkdir(parents=True)
        # Move the file into its new cozy home.
        logging.debug(f"Moving {file.name} to {new_directory}")
        file.rename(new_directory.joinpath(file.name))
        workflow_to_client_map.append(client_name)
        workflow_dummy_id += 1
    return workflow_to_client_map


def rename_output_root_files(output_dir: Path):
    """
    Rename the ROOT files generated by the online clients in the "upload" dir.
    This is to make sure that, after compareDQMOutput.py is run, the resulting
    filenames comply to the the RXOFFLINE regexp used by visDQMUpload, found here:
    https://github.com/cms-sw/cmssw/blob/master/DQMServices/FileIO/python/visDQMUtils.py#L17

    It seems to be enough to swap the run and subsystem parts of the output
    file e.g. renaming DQM_V0001_Info_R000387878.root to DQM_V0001_R000387878_Info.root
    """
    files = list(output_dir.glob("*.root"))
    logging.debug(f"Found {len(files)} root files in {output_dir}")
    for file in files:
        _, version, subsystem, run_and_ext = file.name.split("_")
        run = run_and_ext.split(".")[0]
        new_filename = f"DQM_{version}_{run}_{subsystem}.root"
        logging.debug(f"Renaming {file.name} to {new_filename} in {file.parent}")
        file.rename(file.parent.joinpath(new_filename))


def compare_results(
    base_release_path: Path,
    comp_release_path: Path,
    pr_list: list,
    output_dir: Path,
    num_jobs: int,
    dqmgui_url: str,
    label: str,
) -> Path:
    """
    Runs the comparison of the files found in base_release_path and
    comp_release_path. Puts the results in a newly created subdir inside
    the output_dir.

    Finally, it uploads the comparison files to the dqmgui_url supplied.

    Returns the newly created subdirectory in the output_dir.
    """
    # Create a new directory in the output_dir with the name of the releases and a timestamp.
    output_dir = output_dir.joinpath(
        f"{label}_{base_release_path.name}_vs_{comp_release_path.name}"
    )
    output_dir.mkdir(parents=True)

    # Run the comparison between the base and comp releases
    # using the compareDQMOutput.py script in the *base* release (not that
    # it would make any difference if we used the comp one).
    # cmswrapper.sh is required to activate the cms environment (equivalent
    # to running cmsenv).
    cmd = (
        f"{base_release_path}/cmswrapper.sh compareDQMOutput.py"
        + f" --base-dir {base_release_path.joinpath(label)}"
        + f" --pr-dir {comp_release_path.joinpath(label)}"
        + f" --output-dir {output_dir}"
        + f" --nprocs {num_jobs}"
        + f" --pr-list /#{'#'.join(pr_list)}/"  # compareDQMOutput does some weird stuff
        + f" --test-number {label}"
        + f" --dqmgui-url {dqmgui_url}"
    )
    log_path = output_dir.joinpath("comparison.log")
    logging.info(f"Running: {cmd}. Logs can be found in {log_path}")
    exit_code = 0
    with open(log_path, "w+") as f:
        p = Popen(split(cmd), cwd=base_release_path, text=True, stdout=f, stderr=f)
        exit_code = p.wait()
    if exit_code:
        raise Exception(f"Command {cmd} returned exit code {exit_code}")

    resulting_html = base_release_path.joinpath("dqm-histo-comparison-summary.html")
    assert (
        resulting_html.is_file()
    ), f"Could not find the expected {resulting_html.name} file in {base_release_path}"
    # Move html file to output_dir. Use shutil.move because os.rename does not work
    # between drives.
    move(resulting_html, output_dir.joinpath(resulting_html.name))
    logging.info("Comparison ran successfully.")
    return output_dir


def validate_files_before_comparison(
    base_release_path: Path, comp_release_path: Path, label: str
):
    """
    Given a base release path, a comp release path and a label,
    for each directory of output files ("<MAGIC_NUMBER>.<num>_<client>") in the base release,
    make sure that there is an equivalent file of the same name in the comp release.
    """
    for path in base_release_path.joinpath(label).glob(f"{MAGIC_NUMBER}.*_*"):
        if not path.is_dir():
            continue
        assert comp_release_path.joinpath(
            label, path.name
        ).is_dir(), f"Could not find expected directory {path.name} in {comp_release_path.joinpath(label)}"

        for file in path.glob("*.root"):
            assert comp_release_path.joinpath(
                label, path.name, file.name
            ).is_file(), f"Could not find expected file {file.name} in {comp_release_path.joinpath(label, path.name)}"


def butcher_comparison_html(
    html_file: Path,
    workflow_to_client_map: list,
    dqmgui_url: str,
    dqmgui_url_alias: str = "",
):
    """
    Another machete to replace the dummy workflow ids (assigned at rename_upload_dirs)
    with the actual clients that generated the ROOT file.

    We could have used beautifulsoup, but why add another dependency?!
    """
    assert html_file.is_file()
    text = ""
    with open(html_file, "r") as f:
        text = f.read()
        # Replace the DQMGUI url with the alias, if supplied
        if dqmgui_url_alias:
            text = text.replace(dqmgui_url, dqmgui_url_alias)
    for i in range(0, len(workflow_to_client_map)):
        text = re.sub(
            rf"{MAGIC_NUMBER}\.{i:02d}",
            workflow_to_client_map[i],
            text,
            flags=re.MULTILINE,
        )

    with open(html_file, "w") as f:
        f.write(text)


if __name__ == "__main__":
    args = parse_args()
    configure_logging(verbose=args.verbose)
    preliminary_checks(args)
    logging.info(
        f"Comparing release {args.base_release}"
        + f"{' with PRs '+ str(args.pull_requests_base) if args.pull_requests_base else ''}"
        + f" vs release {args.comparison_release}"
        + f"{' with PRs '+ str(args.pull_requests_comp) if args.pull_requests_comp else ''}"
    )
    # A common label to create subdirectories for results and output and
    # help distinguish between executions of the same comparison.
    common_label = datetime.now().strftime("%Y%m%d-%H%M%S%Z")
    # Split input_dir to the actual dir and the run number.
    input_dir, run_number = args.input_dir.parent, int(args.input_dir.name[3:])
    # Store the full paths to the base and comp releases.
    release_dirs = []

    # A list of client names. Each client's index in the list
    # corresponds to the dummy wofklow id that they will be given.
    # Used to butcher the HTML report later on.
    workflow_to_client_map = []

    # Create a release and run the clients. This is done twice: once for
    # the base release, once for the comparison one.
    for label, release_name, gt_hlt, gt_express, pull_requests in [
        (
            "base",
            args.base_release,
            args.gt_hlt_base,
            args.gt_express_base,
            args.pull_requests_base,
        ),
        (
            "comp",
            args.comparison_release,
            args.gt_hlt_comp,
            args.gt_express_comp,
            args.pull_requests_comp,
        ),
    ]:
        logging.info(f"Creating and building {label} release")
        try:
            release_path = create_release(
                release_name=release_name,
                cmssw_deploy_path=args.cmssw_deploy_path,
                pull_requests=pull_requests,
                working_dir=args.working_dir,
                gt_hlt=gt_hlt,
                gt_express=gt_express,
                build=True,
                reuse_release=args.reuse_releases,
                label=label,
            )
        except Exception:
            logging.critical(f"Error creating release {release_name}", exc_info=True)
            sys.exit(1)

        release_dirs.append(release_path)
        logging.info(f"Release created at {release_path}")
        if args.dont_run_clients:
            logging.info("Not running DQM clients as per user's request")
        else:
            logging.info(f"Running clients for {label} release")

            run_clients(
                release_path=release_path,
                input_dir=input_dir,
                run_number=run_number,
                run_key=args.run_key,
                num_jobs=args.num_jobs,
                label=common_label,
            )
            rename_output_root_files(output_dir=release_path.joinpath("upload"))
            workflow_to_client_map = rename_upload_dirs(
                release_path=release_path, label=common_label
            )

    validate_files_before_comparison(
        base_release_path=release_dirs[0],
        comp_release_path=release_dirs[1],
        label=common_label,
    )

    logging.info("Starting comparison")
    # Get a list of of the PRs in effect. This is for display only and
    # does not really affect the comparison is made. We use a set to guarantee uniqueness.
    unique_prs = {
        *(
            [str(int(pr)) for pr in args.pull_requests_base.split(",")]
            if args.pull_requests_base
            else []
        ),
        *(
            [str(int(pr)) for pr in args.pull_requests_comp.split(",")]
            if args.pull_requests_comp
            else []
        ),
    }

    output_dir = compare_results(
        base_release_path=release_dirs[0],
        comp_release_path=release_dirs[1],
        pr_list=sorted(list(unique_prs)),
        output_dir=args.output_dir,
        num_jobs=args.num_jobs,
        dqmgui_url=args.dqmgui_url,
        label=common_label,
    )
    # Revert dummy workflows to client names
    butcher_comparison_html(
        html_file=output_dir.joinpath("dqm-histo-comparison-summary.html"),
        workflow_to_client_map=workflow_to_client_map,
        dqmgui_url=args.dqmgui_url,
        dqmgui_url_alias=args.dqmgui_url_alias,
    )
