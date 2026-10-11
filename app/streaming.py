"""
Re-exports auth and project primitives for backwards-compatibility.
For real project/key storage and resolution, see `app.auth`.
"""

from __future__ import annotations

from app.auth import (  # noqa: F401
    APIKeyInfo,
    APIKeyStore,
    AuthError,
    InMemoryAPIKeyStore,
    PostgresAPIKeyStore,
    Project,
    extract_api_key,
    generate_api_key,
    hash_key,
    mask_key,
)
