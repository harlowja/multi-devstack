[[local|localrc]]

SERVICE_TOKEN={{ SERVICE_TOKEN }}
ADMIN_PASSWORD={{ ADMIN_PASSWORD }}
MYSQL_PASSWORD={{ DATABASE_PASSWORD }}
RABBIT_PASSWORD={{ RABBIT_PASSWORD }}
SERVICE_PASSWORD={{ SERVICE_PASSWORD }}
DATABASE_PASSWORD={{ DATABASE_PASSWORD }}

LOGFILE=/opt/stack/logs/stack.sh.log
VERBOSE=True
LOG_COLOR=False
SCREEN_LOGDIR=/opt/stack/logs
GIT_BASE=${GIT_BASE:-https://git.openstack.org}

# Only turn on a piece of nova
ENABLED_SERVICES=key,glance,neutron,nova
ENABLE_DEBUG_LOG_LEVEL=true
USE_VENV=true
DATABASE_TYPE=mysql

# Cells!
ENABLED_SERVICES+=,n-cell,n-api-meta
DISABLED_SERVICE+=,n-cpu,n-net,n-sch
