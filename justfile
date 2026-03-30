# DavHome Development Orchestration

# Show available commands
default:
    @just --list

# Run all verification checks (format, clippy, test)
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
    @echo "3. Running tests..."
    @SQLX_OFFLINE=true cargo test
    @echo "✓ Tests OK"
    @echo ""
    @echo "All verification checks passed!"

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
    @DATABASE_URL=postgres://davhome:davhome@localhost:5432/davhome ~/.cargo/bin/sqlx migrate run
    @echo "Generating SQLx query cache..."
    @DATABASE_URL=postgres://davhome:davhome@localhost:5432/davhome cargo sqlx prepare
    @echo "SQLx query cache updated successfully!"
    @echo "Don't forget to commit the .sqlx/ directory"
