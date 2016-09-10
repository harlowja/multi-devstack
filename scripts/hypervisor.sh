#!/bin/bash

if [[ $EUID -ne 0 ]]; then
    echo "This script should not be run using sudo or as the root user"
    exit 1
fi

set -x
set -u

# Doing things with python (virtualenvs or other) typically requires
# the following, so make sure we have these installed.
yum install -y python-devel python-pip python-virtualenv python-tox
yum install -y libffi-devel libvirt-devel openssl-devel mysql-devel \
               postgresql-devel libxml2-devel libxslt-devel openldap-devel
yum install -y qemu-kvm

devstack_branch=stable/liberty
git clone https://github.com/openstack-dev/devstack.git
cd devstack
git checkout $devstack_branch

# NOTE(harlowja):
#
# This seems required or later devstack service stop/start bork,
# and it seems harmless otherwise (perhaps something busted
# in our images); at least iptables service borked and this
# fixed that.
touch /etc/sysconfig/iptables

cat << EOF > localrc

# Please god don't let this get anywhere else...
# This only currently needed since nova needs to know where
# keystone is and we have to give it that info (thus the ordering)
# dependency between the keystone script and this script.
ADMIN_PASSWORD=password

ENABLED_SERVICES=n-cpu
VIRT_DRIVER=qemu
ENABLE_DEBUG_LOG_LEVEL=true
USE_VENV=true

EOF

./stack.sh

