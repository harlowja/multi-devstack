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

LOGFILE=/opt/stack/logs/stack.sh.log
VERBOSE=True
LOG_COLOR=False
SCREEN_LOGDIR=/opt/stack/logs
GIT_BASE=${GIT_BASE:-https://git.openstack.org}

ENABLE_DEBUG_LOG_LEVEL=true
USE_VENV=true
ENABLED_SERVICES=rabbit
