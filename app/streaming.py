"""
API key authentication and project/tier lookup.

The store is behind an interface (same pattern as ProviderAdapter) so
main.py — and later, quota.py — depend on "resolve this key to a
project" without caring whether that lookup hits Postgres, Redis, or
(for now, since neither is wired up yet) an in-memory dict. Swapping the
in-memory store for a real one later means changing this file only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Project:
    """Everything downstream code needs to know about who's calling,
    resolved once per request from the API key."""

    project_id: str
    tier: str  # "free" | "builder" | "team" — drives limits in quota.py later


class AuthError(Exception):
    """Raised when an API key is missing, malformed, or unknown.
    Mirrors ProviderError's role: one exception type the gateway
    catches in one place rather than each store inventing its own."""


@runtime_checkable
class APIKeyStore(Protocol):
    async def resolve(self, api_key: str) -> Project:
        """Look up the project behind an API key. Raises AuthError if
        the key doesn't resolve to anything — never returns a
        placeholder/default Project, since that would mean an invalid
        key silently gets treated as valid."""
        ...


class InMemoryAPIKeyStore:
    """Stand-in until Postgres exists (roadmap step 3). Hardcode a
    couple of test keys here for local development — replace this whole
    class, not patch it, once there's a real projects table to query."""

    def __init__(self) -> None:
        self._keys: dict[str, Project] = {
            "sk-test-local": Project(project_id="local-dev", tier="free"),
        }

    async def resolve(self, api_key: str) -> Project:
        project = self._keys.get(api_key)
        if project is None:
            raise AuthError("Invalid API key")
        return project


def extract_api_key(authorization_header: str | None) -> str:
    """Pulls the key out of a standard 'Authorization: Bearer <key>'
    header. Split out as its own function so it's testable without
    spinning up FastAPI, and so the "Bearer " parsing lives in exactly
    one place."""

    if not authorization_header:
        raise AuthError("Missing Authorization header")

    scheme, _, key = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not key:
        raise AuthError("Authorization header must be 'Bearer <api_key>'")

    return key