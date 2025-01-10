# `fff_dqmtools`

`fff_dqmtools` aka DQM^2.

Page with more information [here](https://cms-dqmdc.docs.cern.ch/software/fff_dqmtools/index.html).

## Building a package

> [!NOTE]
> The `makerpm.sh` script will only work on RHEL-like OSes (e.g. an RHEL8 OpenStack VM).
> To run this script you will need to install `rpmdevtools` and `rpmlint`.

- Create an RPM by executing `./utils/makerpm.sh` in the repository's root folder.

## Installation

### All machines at once

Installation on P5 machines is done on the `cmsdropboxcc8.cms` machine, using the `dropbox2` command (see more information about the dropbox [here](https://twiki.cern.ch/twiki/bin/view/CMS/ClusterUsersGuide#How_to_use_the_dropbox_computer)) and an `.rpm` archive, created using the `./utils/makerpm.sh` script.

This procedure applies the update to all of the DQM machines at once. You will need to be in the appropriate `*_librarian` group. Open a ticket to CMSONS to be added to the group.

Procedure:

1. Copy the `.rpm` you generated to a directory inside a dedicatated directory inside your home directory of the P5 cluster (e.g., `/nfshome0/<your username>/fff_dqmtools`).
2. Then:
  ```bash
    ssh <your P5 username>cmsdropboxcc8.cms
    sudo dropbox2 -z cms -o cc8 -s dqm -u /nfshome0/<your username>/fff_dqmtools/
  ```
> [!NOTE]
> Once the operation completes, you will receive a report of what machines were updated.

> [!NOTE]
> The `fff_dqmtools` service is restarted after the rpm is reinstalled.

### Manually (and remotely)

For the manual remote installation at P5, copy the source code and the built package and then execute: `./utils/install.py --remote <machine_name>` in the repository's root folder. This installation will be undone by puppet days/months later.

## Developing

Openstack allows for great flexibility and access to CMSSW tools.

### `cmssw_deploy`

1. Create an OpenStack machine (currently suggested image: RHEL8).
2. Install `locmap` (see [here](https://linux.web.cern.ch/rhel/locmap/)). You now have CVMFS.
3. Install required tools (`yum install -y git gcc python38`). You might need to `ln -s /usr/bin/python3 ~/bin/python`.
4. Switch to your CERN user (`su <username`).
5. Clone `fff_dqmtools`.
6. `source /cvmfs/cms.cern.ch/cmsset_default.sh # You now have scram`
7. The openstack machine must have the SSH keys you use to authenticate with github (so that `scram` can do whatever needed to clone CMSSW).
8. Configure `git` (`git config --global user.name`, as well as `user.email`).

You should now be ready to run `cmssw_deploy`.

## DQM^2 workflows

### To update DQM^2 DB with client info

Create JSON report files in:
https://github.com/cms-DQM/fff_dqmtools/blob/a62a1a317e1bc312c704065a43d3bd0aeb5ecc74/applets/analyze_files.py#L100
Then read JSONs in folder:
https://github.com/cms-DQM/fff_dqmtools/blob/a62a1a317e1bc312c704065a43d3bd0aeb5ecc74/applets/fff_filemonitor.py#L181
And upload with http:
https://github.com/cms-DQM/fff_dqmtools/blob/a62a1a317e1bc312c704065a43d3bd0aeb5ecc74/applets/fff_filemonitor.py#L82
To be catched with web client:
https://github.com/cms-DQM/fff_dqmtools/blob/a62a1a317e1bc312c704065a43d3bd0aeb5ecc74/applets/fff_web.py#L462
And saved to DB:
https://github.com/cms-DQM/fff_dqmtools/blob/a62a1a317e1bc312c704065a43d3bd0aeb5ecc74/applets/fff_web.py#L148
