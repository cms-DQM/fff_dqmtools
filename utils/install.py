#!/usr/bin/env python3

import sys
import subprocess
import os
import glob
import subprocess

sys.dont_write_bytecode = True


def call(*kargs, **kwargs):
    print("Running:", kargs)
    r = subprocess.call(*kargs, **kwargs)
    print("Finished command:", r)


def install_local(rpm):
    ### main install
    call(["sudo /etc/init.d/fff_dqmtools stop"], shell=True)
    call(["sudo yum -y remove fff-dqmtools"], shell=True)
    call(["sudo rm -frv /opt/fff_dqmtools"], shell=True)
    call(["sudo", "yum", "-y", "install", rpm])

    # call(["sudo rm -frv /var/lib/fff_dqmtools"], shell=True)

    # restart hltd
    # call(["sudo", "/sbin/service", "hltd", "restart"])

    ### reset init
    ##call(["sudo /sbin/chkconfig --del fff_dqmtools"], shell=True)
    ##call(["sudo /sbin/chkconfig --add fff_dqmtools"], shell=True)
    ##call(["sudo /sbin/chkconfig fff_dqmtools reset"], shell=True)
    ##call(["sudo /sbin/chkconfig fff_dqmtools resetpriorities"], shell=True)
    ##call(["sudo ls -la /etc/rc.d/*/*fff_*"], shell=True)


def install_remote(spath, host):
    print("*" * 80)
    print("* Installing on:", host)
    print("*\n" * 7)
    print("*" * 80)

    subprocess.call(["ssh", host, "python", spath, "--local"])


if __name__ == "__main__":
    # cd to the current directory
    spath = os.path.abspath(__file__)
    os.chdir(os.path.dirname(spath))

    rpms = glob.glob("../tmp/RPMBUILD/RPMS/x86_64/*.rpm")
    if not len(rpms):
        rpms = glob.glob("*.rpm")

    rpm = None
    if len(rpms) == 1:
        rpm = rpms[0]
        print("RPM:", rpm)
    else:
        print("RPM not found, do ./makerpm.sh")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "--local":
        sys.exit(install_local(rpm))
    elif len(sys.argv) > 1 and sys.argv[1] == "--remote":
        if len(sys.argv) > 2:
            hosts = sys.argv[2:]
        else:
            sys.path.append("../")
            import fff_cluster

            all = []
            for k, hosts in fff_cluster.get_node()["_all"].items():
                print(k + ":", " ".join(hosts))
                all += hosts

            print("all:", " ".join(all))
            sys.exit(1)

        for host in hosts:
            install_remote(spath, host)
    else:
        print("Please provide either --local or --remote")
