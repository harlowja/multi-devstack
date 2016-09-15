[[local|localrc]]

SERVICE_TOKEN={{ SERVICE_TOKEN }}
ADMIN_PASSWORD={{ ADMIN_PASSWORD }}
MYSQL_PASSWORD={{ DATABASE_PASSWORD }}
RABBIT_PASSWORD={{ RABBIT_PASSWORD }}
SERVICE_PASSWORD={{ SERVICE_PASSWORD }}
DATABASE_PASSWORD={{ DATABASE_PASSWORD }}
DATABASE_HOST={{ DATABASE_HOST }}
RABBIT_HOST={{ RABBIT_HOST }}
RABBIT_USER={{ RABBIT_USER }}
DATABASE_USER={{ DATABASE_USER }}

# Legacy?
MYSQL_HOST={{ DATABASE_HOST }}
MYSQL_USER={{ DATABASE_USER }}

LOGFILE=/opt/stack/logs/stack.sh.log
VERBOSE=True
LOG_COLOR=False
SCREEN_LOGDIR=/opt/stack/logs
GIT_BASE=${GIT_BASE:-https://git.openstack.org}

ENABLE_DEBUG_LOG_LEVEL=true
USE_VENV=true
DATABASE_TYPE=mysql
VIRT_DRIVER=libvirt
LIBVIRT_TYPE=qemu

# For now use this driver, switch to PyMySQL later...
MYSQL_DRIVER=MySQL-python

ENABLED_SERVICES=nova
ENABLED_SERVICES+=n-api-meta,n-cpu
DISABLED_SERVICE+=,n-net,n-sch,n-cell,n-api,n-obj,n-novnc,n-xvnc,n-spice
DISABLED_SERVICE+=,n-crt,n-cauth,n-sproxy
DISABLED_SERVICE+=,mysql,postgresql
