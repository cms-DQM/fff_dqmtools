#!/bin/bash -e

SCRIPTDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && cd .. && pwd )"
BUILDDIR="$SCRIPTDIR/tmp/"

echo "Our directory: $SCRIPTDIR"
echo "Creating build dir: $BUILDDIR"
rm -fr $BUILDDIR
mkdir -p $BUILDDIR
cd $BUILDDIR

cat > fff-dqmtools.spec <<EOF
Name: fff-dqmtools
Version: 1.9.4
Release: 1
Summary: DQM tools for FFF.
License: gpl
Group: DQM
Packager: micius
Source: none
%define _tmppath $BUILDDIR/build-tmp
BuildRoot: %{_tmppath}
BuildArch: x86_64
AutoReqProv: no
Provides:/opt/fff_dqmtools
Provides:/etc/logrotate.d/fff_dqmtools
Provides:/etc/init.d/fff_dqmtools
Requires:python36, python3-gevent >= 1.2.2, python3-requests
%description
DQM tools for FFF and new DQM machines.
%prep
%build
%install

mkdir -p \$RPM_BUILD_ROOT/opt/fff_dqmtools
mkdir -p \$RPM_BUILD_ROOT/opt/fff_dqmtools/lib
mkdir -p \$RPM_BUILD_ROOT/opt/fff_dqmtools/lib/inotify
mkdir -p \$RPM_BUILD_ROOT/opt/fff_dqmtools/lib/ws4py
mkdir -p \$RPM_BUILD_ROOT/opt/fff_dqmtools/applets

mkdir -p \$RPM_BUILD_ROOT/opt/fff_dqmtools/misc
mkdir -p \$RPM_BUILD_ROOT/opt/fff_dqmtools/utils
mkdir -p \$RPM_BUILD_ROOT/opt/fff_dqmtools/web.static

mkdir -p \$RPM_BUILD_ROOT/etc/logrotate.d
mkdir -p \$RPM_BUILD_ROOT/etc/init.d
mkdir -p \$RPM_BUILD_ROOT/var/lib/fff_dqmtools

install -m 755 $SCRIPTDIR/*.py -t \$RPM_BUILD_ROOT/opt/fff_dqmtools/
install -m 644 $SCRIPTDIR/lib/*.py -t \$RPM_BUILD_ROOT/opt/fff_dqmtools/lib/
install -m 644 $SCRIPTDIR/lib/*.egg -t \$RPM_BUILD_ROOT/opt/fff_dqmtools/lib/
install -m 644 $SCRIPTDIR/lib/inotify/*.py -t \$RPM_BUILD_ROOT/opt/fff_dqmtools/lib/inotify
install -m 644 $SCRIPTDIR/applets/*.py -t \$RPM_BUILD_ROOT/opt/fff_dqmtools/applets/

cp -r $SCRIPTDIR/misc -t \$RPM_BUILD_ROOT/opt/fff_dqmtools/
cp -r $SCRIPTDIR/utils -t \$RPM_BUILD_ROOT/opt/fff_dqmtools/
cp -r $SCRIPTDIR/web.static -t \$RPM_BUILD_ROOT/opt/fff_dqmtools/
cp -r $SCRIPTDIR/lib/ws4py -t \$RPM_BUILD_ROOT/opt/fff_dqmtools/lib/

install -m 755 $SCRIPTDIR/misc/fff_dqmtools -t \$RPM_BUILD_ROOT/etc/init.d/
install -m 644 $SCRIPTDIR/misc/fff_dqmtools.logrotate \$RPM_BUILD_ROOT/etc/logrotate.d/fff_dqmtools

%files
%defattr(-, root, root, -)
/opt/fff_dqmtools/*.py
/opt/fff_dqmtools/lib/*.py
/opt/fff_dqmtools/lib/*.egg
/opt/fff_dqmtools/lib/inotify/*.py
/opt/fff_dqmtools/applets/*.py
/opt/fff_dqmtools/lib/ws4py

/opt/fff_dqmtools/misc
/opt/fff_dqmtools/utils
/opt/fff_dqmtools/web.static

/etc/logrotate.d/fff_dqmtools
/etc/init.d/fff_dqmtools

%defattr(-, root, dqmpro, 775)
/var/lib/fff_dqmtools

%post
if [ -x /usr/lib/lsb/install_initd ]; then
  /usr/lib/lsb/install_initd /etc/init.d/fff_dqmtools
else
  /sbin/chkconfig --add fff_dqmtools
fi

/etc/init.d/fff_dqmtools restart

%global __os_install_post %(echo '%{__os_install_post}' | sed -e 's!/usr/lib[^[:space:]]*/brp-python-bytecompile[[:space:]].*\$!!g')
EOF

mkdir -p RPMBUILD/{RPMS/{noarch},SPECS,BUILD,SOURCES,SRPMS}
rpmbuild --define "_topdir `pwd`/RPMBUILD" -bb fff-dqmtools.spec
#rm -rf patch-cmssw-tmp
