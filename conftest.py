import os

# Backend tests build their own SQLite engines/sessions in fixtures, but they
# import the app (and therefore services.api.db) at module import time without
# setting DATABASE_URL. Opt in to the explicit SQLite dev fallback so that
# import doesn't fail fast; the fallback engine itself is never used by tests.
os.environ.setdefault("KNIGHTMIND_DEV_SQLITE", "1")
