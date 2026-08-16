"""Test configuration.

The current suite covers pure logic (streak maths, lesson validation and SQL
generation) and needs no database. If you add tests that hit Postgres, set
TEST_DATABASE_URL to a scratch database — never the one the app uses.
"""

import os

_TEST_URL = os.environ.get("TEST_DATABASE_URL")
_APP_URL = os.environ.get("DATABASE_URL")

if _TEST_URL:
    if _TEST_URL == _APP_URL:
        raise RuntimeError(
            "TEST_DATABASE_URL matches DATABASE_URL. Refusing to run against the app database."
        )
    # Must be set before app.db builds its engine at import time.
    os.environ["DATABASE_URL"] = _TEST_URL
