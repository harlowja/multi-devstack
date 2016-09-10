#!/bin/bash

# This will setup what godaddy calls a <map> service,
# it houses the equivalent of the top level services and
# the top level nova cell entrypoint (we are also co-hosting)
# the database here (but that's not typical).

set -x
set -u

# Doing things with python (virtualenvs or other) typically requires
# the following, so make sure we have these installed.
sudo yum install -y python-devel
sudo yum install -y libffi-devel libvirt-devel openssl-devel mysql-devel \
                    postgresql-devel libxml2-devel libxslt-devel openldap-devel

# We will be setting up mysql here.
sudo yum install -y mysql-server
sudo systemctl start mysqld

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

ENABLED_SERVICES=key,glance,neutron
ENABLE_DEBUG_LOG_LEVEL=true
USE_VENV=true
DATABASE_TYPE=mysql

EOF

./stack.sh

# You will be prompted for a DB PASSWORD, SERVICE_TOKEN, SERVICE_PASSWORD, enter one (or let it auto-generate)
#
# Later run the following to get these for later usage.
#
#


