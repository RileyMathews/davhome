# DavHome Development Orchestration

db_url := "postgres://davhome:davhome@localhost:5432/davhome"

# Show available commands
default:
    @just --list

# Run all verification checks (format, clippy, test-unit)
verify:
    @echo "Running verification checks..."
    @echo ""
    @echo "1. Checking code formatting..."
    @cargo fmt --check
    @echo "✓ Formatting OK"
    @echo ""
    @echo "2. Running clippy..."
    @SQLX_OFFLINE=true cargo clippy -- -D warnings
    @echo "✓ Clippy OK"
    @echo ""
    @echo "3. Running unit tests..."
    @just test-all
    @echo "✓ tests OK"
    @echo ""
    @echo "All verification checks passed!"

# Run fast library unit tests only
test-unit:
    @SQLX_OFFLINE=true cargo test --lib

# Run all repository tests against Postgres
test-repo:
    @DATABASE_URL={{db_url}} cargo test --test repository_users --test repository_calendars

# Run all HTTP/router tests against Postgres
test-http:
    @DATABASE_URL={{db_url}} cargo test --test http_auth --test http_calendars

# Run the full test suite against Postgres
test-all:
    @DATABASE_URL={{db_url}} cargo test

# Update SQLx offline query cache
# Ensures dockerized postgres is running, runs migrations, and generates query cache
update-sqlx:
    @echo "Checking PostgreSQL container status..."
    @if ! docker ps | grep -q davhome-postgres; then \
        echo "PostgreSQL container not running. Starting docker-compose..."; \
        docker-compose up -d; \
        echo "Waiting for PostgreSQL to be healthy..."; \
        until docker ps | grep davhome-postgres | grep -q healthy; do sleep 1; done; \
        echo "PostgreSQL is ready!"; \
    else \
        echo "PostgreSQL container is already running"; \
    fi
    @echo "Running migrations..."
    @DATABASE_URL={{db_url}} ~/.cargo/bin/sqlx migrate run
    @echo "Generating SQLx query cache..."
    @DATABASE_URL={{db_url}} cargo sqlx prepare
    @echo "SQLx query cache updated successfully!"
    @echo "Don't forget to commit the .sqlx/ directory"

litmus-test:
	nix develop path:.#litmus -c litmus "http://127.0.0.1:3000/dav/calendars/user01/" "user01" "1234567890123456"
