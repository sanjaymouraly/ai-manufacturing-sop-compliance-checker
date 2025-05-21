#!/bin/sh
# Container entrypoint: get the database into a usable state, then exec the
# command. Safe to run on every container start - migration is idempotent and
# seeding is skipped when data already exists.
set -eu

echo "==> Applying database migrations"
python /app/scripts/migrate.py

# SEED_ON_START=true populates the synthetic demo dataset on a fresh volume.
# It never overwrites an existing database.
if [ "${SEED_ON_START:-false}" = "true" ]; then
  if python - <<'PY'
import sys
sys.path.insert(0, "/app/backend")
from sqlalchemy import create_engine, func, select
from app.config import get_settings
from app.models.entities import SopDocument
engine = create_engine(get_settings().sqlalchemy_url)
with engine.connect() as conn:
    count = conn.execute(select(func.count(SopDocument.id))).scalar() or 0
sys.exit(0 if count == 0 else 1)
PY
  then
    echo "==> Empty database detected - generating and seeding the synthetic dataset"
    python /app/scripts/generate_dataset.py
    python /app/scripts/seed.py
  else
    echo "==> Database already contains documents - skipping seed"
  fi
fi

echo "==> Starting: $*"
exec "$@"
