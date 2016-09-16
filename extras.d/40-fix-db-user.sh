# Something is busted with mysql and the default user, so we'll just fix it..

if [[ "$1" == "stack" && "$2" == "install" ]]; then
    if is_service_enabled mysql; then
        echo "Fixing mysql user privileges..."
        start_service mariadb
        sudo mysqladmin -u root password $DATABASE_PASSWORD || true
        sudo mysql -uroot -p$DATABASE_PASSWORD -h127.0.0.1 -e "GRANT ALL PRIVILEGES ON *.* TO '$MYSQL_USER'@'$MYSQL_HOST' identified by '$MYSQL_PASSWORD';"
        sudo mysql -uroot -p$DATABASE_PASSWORD -h127.0.0.1 -e "GRANT ALL PRIVILEGES ON *.* TO '$MYSQL_USER'@'localhost' identified by '$MYSQL_PASSWORD';"
        restart_service mariadb
    fi
fi
