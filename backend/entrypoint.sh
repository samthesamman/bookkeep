#!/bin/bash

# Set DATABASE_URL explicitly to ensure it points to the correct location
export DATABASE_URL="${DATABASE_URL:-sqlite:////app/data/bookkeep.db}"

if [[ "$DATABASE_URL" == sqlite:* ]]; then
    # Ensure data directory exists (where SQLite DB will be created)
    mkdir -p /app/data
    chmod 755 /app/data
fi

# Set PYTHONPATH - Alembic needs to import app.* modules
export PYTHONPATH="/app/backend:$PYTHONPATH"

# Create tables first (SQLAlchemy will create all tables with current schema)
# Then migrations will add any missing columns (idempotent)
cd /app
echo "Creating database tables if they don't exist..."
# NOTE: app.models MUST be imported so every model registers on Base.metadata.
# Importing only app.database leaves Base.metadata empty and create_all() is a
# no-op, which is why fresh databases ended up with no 'books' table and the
# unguarded migrations (005+) then failed with "relation books does not exist".
python -c "from app.database import engine, Base; import app.models; Base.metadata.create_all(bind=engine); print('Tables created/verified')"

echo "Running database migrations..."
echo "DATABASE_URL: $DATABASE_URL"

cd /app/backend

# Check current Alembic tracking state
echo "Checking Alembic version..."
ALEMBIC_CURRENT=$(python -m alembic -c alembic.ini current 2>&1)
echo "Alembic current: $ALEMBIC_CURRENT"

# Determine whether the database is already tracked by Alembic by inspecting the
# alembic_version table directly.  We must NOT infer this from `alembic current`
# output: a validly-tracked DB that is simply *behind* head (the normal state
# right after a new migration is added) prints only the bare revision with no
# "(head)" / "Rev:" marker, which previously caused a false "untracked" result
# and a stamp-to-head that silently skipped the pending migration.
ALEMBIC_TRACKED=$(python -c "
import os
from sqlalchemy import inspect, create_engine
engine = create_engine(os.environ['DATABASE_URL'])
try:
    if inspect(engine).has_table('alembic_version'):
        with engine.connect() as conn:
            row = conn.exec_driver_sql('SELECT version_num FROM alembic_version').fetchone()
        print('yes' if row and row[0] else 'no')
    else:
        print('no')
except Exception:
    print('no')
" 2>/dev/null || echo "no")
echo "Alembic tracked: $ALEMBIC_TRACKED"

# If there is no alembic_version table the DB was bootstrapped via SQLAlchemy
# create_all without Alembic ever having run (legacy deployments).  We detect
# this and stamp the database at the correct revision so Alembic only runs the
# truly new migrations.
if [ "$ALEMBIC_TRACKED" != "yes" ]; then
    echo "No Alembic version found. Checking whether the database already has tables..."

    TABLES_EXIST=$(python -c "
import sys, os
sys.path.insert(0, '/app/backend')
from sqlalchemy import inspect, create_engine
engine = create_engine(os.environ['DATABASE_URL'])
try:
    print('yes' if inspect(engine).has_table('users') else 'no')
except Exception:
    print('no')
" 2>/dev/null || echo "no")

    echo "Existing tables found: $TABLES_EXIST"


    if [ "$TABLES_EXIST" = "yes" ]; then
        # The database has data but was never tracked by Alembic.
        # Check whether oidc_subject already exists to decide which revision to
        # stamp so we don't re-run migrations that were applied via create_all.
        OIDC_COL=$(python -c "
import sys, os
sys.path.insert(0, '/app/backend')
from sqlalchemy import inspect, create_engine
engine = create_engine(os.environ['DATABASE_URL'])
try:
    cols = [c['name'] for c in inspect(engine).get_columns('users')]
    print('yes' if 'oidc_subject' in cols else 'no')
except Exception:
    print('no')
" 2>/dev/null || echo "no")

        if [ "$OIDC_COL" = "yes" ]; then
            # Schema is fully up to date; stamp as head so no migrations run.
            echo "Schema is current. Stamping database as head..."
            python -m alembic -c alembic.ini stamp heads
        else
            # Schema is behind (oidc_subject missing). Stamp at 034 so Alembic
            # runs 035 and 036 to bring the schema up to date.
            echo "Legacy database missing recent columns. Stamping at revision 034..."
            python -m alembic -c alembic.ini stamp 034
        fi
    fi
fi

# Run all pending migrations.  Fail loudly if something goes wrong so the
# problem is visible in container logs rather than silently masked.
echo "Applying pending Alembic migrations..."
if python -m alembic -c alembic.ini upgrade heads; then
    echo "Migrations completed successfully."
    python -m alembic -c alembic.ini current
else
    echo "ERROR: Alembic migrations failed. Check the logs above for details."
    exit 1
fi

# Start the application from /app (where uvicorn expects to find backend module)
cd /app
echo "Starting application..."
exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
