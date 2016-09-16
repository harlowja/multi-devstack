# This is fixed in mitaka and newer, but for now we do this to make
# sure the driver actually gets installed...

if [[ "$1" == "stack" && "$2" == "pre-install" ]]; then
    if [ -n "$DATABASE_TYPE" ]; then
        install_database_python
    fi
fi
