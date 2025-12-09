#!/bin/bash
# PostgreSQL initialization script to create multiple databases
# This script is executed when the PostgreSQL container first starts

set -e
set -u

function create_database() {
	local database=$1
	echo "Checking if database '$database' exists..."
	
	if psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d postgres -lqt | cut -d \| -f 1 | grep -qw "$database"; then
		echo "Database '$database' already exists, skipping creation"
	else
		echo "Creating database '$database'"
		psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d postgres <<-EOSQL
		    CREATE DATABASE $database;
		    GRANT ALL PRIVILEGES ON DATABASE $database TO $POSTGRES_USER;
EOSQL
	fi
}

if [ -n "$POSTGRES_MULTIPLE_DATABASES" ]; then
	echo "Multiple database creation requested: $POSTGRES_MULTIPLE_DATABASES"
	for db in $(echo $POSTGRES_MULTIPLE_DATABASES | tr ',' ' '); do
		create_database $db
	done
	echo "Multiple databases created"
fi
