"""
API key authentication, generation, and project/tier lookup.

Keys are hashed before storage/lookup — even in this in-memory
placeholder — so the security model matches what the real Postgres-backed
store will do (see schema.sql's key_hash column). Never compare or store
raw keys anywhere.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Project:
    project_id: str
    tier: str  # "free" | "builder" | "team" — drives limits in quota.py later


class AuthError(Exception):
    """Raised when an API key is missing, malformed, or unknown."""


def hash_key(raw_key: str) -> str:
    """One-way hash used for both storing and looking up keys. SHA-256
    is fine here — this isn't a password (no brute-forceable human
    pattern behind it), it's a high-entropy random token, so no need
    for bcrypt/argon2's deliberate slowness."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> str:
    """Creates a new raw key to hand to a developer. Returned exactly
    once — only its hash gets stored, so this is the one moment the
    plaintext exists anywhere in the system."""
    return f"sk-live-{secrets.token_urlsafe(32)}"


@runtime_checkable
class APIKeyStore(Protocol):
    async def resolve(self, api_key: str) -> Project:
        """Raises AuthError if the key doesn't resolve to anything —
        never returns a placeholder/default Project."""
        ...


class InMemoryAPIKeyStore:
    """Stand-in until Postgres exists (roadmap step 3). Register keys
    at startup or via register(); replace this whole class, not patch
    it, once there's a real api_keys table to query."""

    def __init__(self) -> None:
        self._keys_by_hash: dict[str, Project] = {}
        # One key seeded for local testing without needing the generator.
        self.register("sk-test-local", Project(project_id="local-dev", tier="free"))

    def register(self, raw_key: str, project: Project) -> None:
        self._keys_by_hash[hash_key(raw_key)] = project

    async def resolve(self, api_key: str) -> Project:
        project = self._keys_by_hash.get(hash_key(api_key))
        if project is None:
            raise AuthError("Invalid API key")
        return project


def extract_api_key(authorization_header: str | None) -> str:
    if not authorization_header:
        raise AuthError("Missing Authorization header")

    scheme, _, key = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not key:
        raise AuthError("Authorization header must be 'Bearer <api_key>'")

    return key