# This is fixed in mitaka and newer, but for now we do this to make
# sure the driver actually gets installed...
#
# See: https://github.com/openstack-dev/devstack/commit/7dd890d6e13

if [[ "$1" == "stack" && "$2" == "pre-install" ]]; then
    if [ -n "$DATABASE_TYPE" ]; then
        install_database_python
    fi
fi
