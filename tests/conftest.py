"""Shared fixtures and mocks for tests.

Mocks heavy external dependencies (WeChat SDK, DB, LLM) so pure-function
tests can import app modules without a running database or network.
"""

import sys
from unittest.mock import MagicMock

# Pre-inject mocks for modules that are unavailable in test env
# before any app module tries to import them.
_MOCK_MODULES = [
    "wecom_aibot_sdk",
    "wecom_aibot_sdk.ws_client",
    "wecom_aibot_sdk.ws_client_options",
    "psycopg2",
    "psycopg2.extensions",
    "pgvector",
    "pgvector.sqlalchemy",
    "fastembed",
]

for mod in _MOCK_MODULES:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()
