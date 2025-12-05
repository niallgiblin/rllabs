#!/bin/bash
# PostgreSQL initialization script to create multiple databases
# This script is executed when the PostgreSQL container first starts

set -e
set -u

function create_database() {
	local database=$1
	echo "Checking if database '$database' exists..."
	
	# Check if database exists
	if psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -lqt | cut -d \| -f 1 | grep -qw "$database"; then
		echo "Database '$database' already exists, skipping creation"
	else
		echo "Creating database '$database'"
		psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
		    CREATE DATABASE $database;
		    GRANT ALL PRIVILEGES ON DATABASE $database TO $POSTGRES_USER;
EOSQL
	fi
}

# Create databases if they don't exist
if [ -n "$POSTGRES_MULTIPLE_DATABASES" ]; then
	echo "Multiple database creation requested: $POSTGRES_MULTIPLE_DATABASES"
	for db in $(echo $POSTGRES_MULTIPLE_DATABASES | tr ',' ' '); do
		create_database $db
	done
	echo "Multiple databases created"
fi
