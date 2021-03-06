SERVICE_TOKEN={{ SERVICE_TOKEN }}
ADMIN_PASSWORD={{ ADMIN_PASSWORD }}
SERVICE_PASSWORD={{ SERVICE_PASSWORD }}

RABBIT_HOST={{ RABBIT_HOST }}
RABBIT_USER={{ RABBIT_USER }}
RABBIT_PASSWORD={{ RABBIT_PASSWORD }}

DATABASE_TYPE=mysql
DATABASE_PASSWORD={{ DATABASE_PASSWORD }}
DATABASE_HOST={{ DATABASE_HOST }}
DATABASE_USER={{ DATABASE_USER }}

# Might as well do this (since it's for dev. anyway).
DATABASE_QUERY_LOGGING=True

MYSQL_DRIVER=PyMySQL
MYSQL_HOST={{ DATABASE_HOST }}
MYSQL_USER={{ DATABASE_USER }}
MYSQL_PASSWORD={{ DATABASE_PASSWORD }}

LOGFILE=/opt/stack/logs/stack.sh.log
VERBOSE=True
LOG_COLOR=False
ENABLE_DEBUG_LOG_LEVEL=true

GIT_BASE=${GIT_BASE:-https://git.openstack.org}
SYSLOG=False
USE_SCREEN=False
LOG_COLOR=False
