#!/bin/bash

# Useful script to reset a mariadb servers root password.

if [ "$EUID" -ne 0 ]; then
    echo "Please run this program with root permissions."
    exit 1
fi

if [ $# -eq 0 ]; then
    echo "Password argument required."
    exit 1
fi

set -x -e

sock_file="/var/lib/mysql/mysql.sock"
pid_file="/var/run/mariadb/mariadb.pid"
new_pass="$1"
sql_file=$(mktemp --suffix ".sql")

cat << EOF > $sql_file
USE mysql;
UPDATE user SET password=PASSWORD("$new_pass") WHERE User='root';
FLUSH privileges;
quit
EOF

function clean_exit(){
    local error_code="$?"
    if [ -f "$sql_file" ]; then
        rm "$sql_file"
    fi
    return $error_code
}

trap "clean_exit" EXIT

systemctl stop mariadb
mysqld_safe --skip-grant-tables &

# Give a little time for mysqld_safe to startup...
#
# To avoid 'Can't connect to local MySQL server through socket '/var/lib/mysql/mysql.sock' (2)'
# and similar issues...
while [ ! -e "$sock_file" ]; do
    echo "Waiting for socket file $sock_file to appear..."
    sleep 5
done

mysql -u root < $sql_file

mysqld_safe=$(cat $pid_file)
echo "Stopping mysqld_safe pid: $mysqld_safe"
kill $mysqld_safe

while [ -e "$sock_file" ]; do
    echo "Waiting for socket file $sock_file to disappear..."
    sleep 5
done

systemctl restart mariadb
