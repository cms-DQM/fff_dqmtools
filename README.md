# `fff_dqmtools`

`fff_dqmtools` aka DQM^2.

Twiki with more information [here](https://twiki.cern.ch/twiki/bin/viewauth/CMS/DQMOnlineFFFTools).

## Building a package

> [!NOTE]
> The `makerpm.sh` script will only work on RHEL-like OSes (e.g. an RHEL8 OpenStack VM).
> To run this script you will need to install `rpmdevtools` and `rpmlint`.

* Create a RPM by executing `./utils/makerpm.sh` in the repository's root folder.

## Installation

* For the manual installation at P5, copy the source code and the built package and then execute: `./utils/install.py --remote <machine_name>` in the respository's root folder. This installation will be undone by puppet days/months later.

* To update all of the DQM machines at once, the [dropbox](https://twiki.cern.ch/twiki/bin/view/CMS/ClusterUsersGuide#How_to_use_the_dropbox_computer) could be used (you will need to be in the appropriate `*_librarian` group):
    ```bash
    ssh cmsdropboxcc8.cms
    sudo dropbox2 -z cms -o cc8 -s dqm -u /folder/with/fff_dqmtools/
    ```
> [!INFO]
> Once the operation completes, you will receive a report of what machines were updated.

## DQM^2 worflows

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
