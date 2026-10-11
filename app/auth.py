"""
API key authentication, generation, and project/tier lookup.

Keys are hashed using SHA-256 before storage and lookup. Plaintext keys are
never stored or compared in plaintext. Only the SHA-256 hash lives in the
Postgres `api_keys` table.

This module provides `PostgresAPIKeyStore` for persistent production storage
and project/key management, replacing the temporary in-memory store.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import asyncpg


@dataclass(frozen=True)
class Project:
    project_id: str
    tier: str = "free"  # "free" | "builder" | "team" | "enterprise"
    name: str = "Default Project"
    owner_id: str | None = None
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class APIKeyInfo:
    id: str
    project_id: str
    name: str
    prefix: str
    is_active: bool
    created_at: datetime
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None


class AuthError(Exception):
    """Raised when an API key is missing, malformed, revoked, or unknown."""


def hash_key(raw_key: str) -> str:
    """One-way hash used for storing and looking up keys. SHA-256 is
    appropriate for high-entropy random API keys."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def mask_key(raw_key: str) -> str:
    """Returns a redacted representation of a key (e.g. 'sk-live-a1b2...3c4d')
    safe for display in logs and dashboards."""
    if len(raw_key) <= 12:
        return raw_key[:4] + "..." if len(raw_key) > 4 else raw_key
    return f"{raw_key[:10]}...{raw_key[-4:]}"


def generate_api_key(prefix: str = "sk-live-") -> str:
    """Creates a new raw key to hand to a developer. Returned exactly
    once — only its hash gets stored."""
    return f"{prefix}{secrets.token_urlsafe(32)}"


def extract_api_key(authorization_header: str | None) -> str:
    """Pulls the key out of a standard 'Authorization: Bearer <key>' header."""
    if not authorization_header:
        raise AuthError("Missing Authorization header")

    scheme, _, key = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not key:
        raise AuthError("Authorization header must be 'Bearer <api_key>'")

    return key.strip()


@runtime_checkable
class APIKeyStore(Protocol):
    async def resolve(self, api_key: str) -> Project:
        """Raises AuthError if the key doesn't resolve to an active project."""
        ...


class PostgresAPIKeyStore:
    """Durable, Postgres-backed API key and Project store.

    Handles SHA-256 hashed lookups, active/revoked validation, key generation,
    and project workspace CRUD.
    """

    def __init__(self, database_url: str, seed_local_key: bool = True) -> None:
        self._database_url = database_url
        self._seed_local_key = seed_local_key
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        """Connect to Postgres pool and ensure tables and test seed exist."""
        self._pool = await asyncpg.create_pool(
            self._database_url,
            statement_cache_size=0,
            min_size=1,
            max_size=5,
        )
        await self._ensure_schema()
        if self._seed_local_key:
            await self._seed_dev_key()

    async def stop(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PostgresAPIKeyStore is not started")
        return self._pool

    async def _ensure_schema(self) -> None:
        """Ensures the projects and api_keys tables exist with required columns."""
        pool = self._require_pool()
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                project_id      TEXT PRIMARY KEY,
                name            TEXT NOT NULL DEFAULT 'Default Project',
                tier            TEXT NOT NULL DEFAULT 'free',
                owner_id        TEXT NULL,
                is_active       BOOLEAN NOT NULL DEFAULT TRUE,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                key_hash        TEXT NOT NULL UNIQUE,
                project_id      TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                name            TEXT NOT NULL DEFAULT 'Default Key',
                prefix          TEXT NOT NULL DEFAULT '',
                is_active       BOOLEAN NOT NULL DEFAULT TRUE,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                revoked_at      TIMESTAMPTZ NULL,
                last_used_at    TIMESTAMPTZ NULL
            );
            """
        )
        await pool.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);")
        await pool.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_project_id ON api_keys(project_id);")

    async def _seed_dev_key(self) -> None:
        """Seeds the local development key 'sk-test-local' if not present."""
        pool = self._require_pool()
        test_hash = hash_key("sk-test-local")
        await pool.execute(
            """
            INSERT INTO projects (project_id, name, tier, is_active)
            VALUES ('local-dev', 'Local Development', 'free', TRUE)
            ON CONFLICT (project_id) DO NOTHING;
            """
        )
        await pool.execute(
            """
            INSERT INTO api_keys (key_hash, project_id, name, prefix, is_active)
            VALUES ($1, 'local-dev', 'Local Dev Key', 'sk-test-local', TRUE)
            ON CONFLICT (key_hash) DO NOTHING;
            """,
            test_hash,
        )

    # ------------------------------------------------------------------
    # Resolution (Hot Path)
    # ------------------------------------------------------------------

    async def resolve(self, api_key: str) -> Project:
        """Resolve a raw API key to its associated Project.

        Verifies the key hash exists, is active, is not revoked, and that
        the parent project is active.
        """
        pool = self._require_pool()
        key_h = hash_key(api_key)

        row = await pool.fetchrow(
            """
            SELECT p.project_id, p.name, p.tier, p.owner_id, p.is_active AS project_active,
                   p.created_at AS project_created_at, p.updated_at AS project_updated_at,
                   k.id AS key_id, k.is_active AS key_active, k.revoked_at
            FROM api_keys k
            JOIN projects p ON p.project_id = k.project_id
            WHERE k.key_hash = $1
            """,
            key_h,
        )

        if row is None:
            raise AuthError("Invalid API key")

        if row["revoked_at"] is not None or not row["key_active"]:
            raise AuthError("API key has been revoked")

        if not row["project_active"]:
            raise AuthError("Project is inactive")

        # Async fire-and-forget last_used_at update
        asyncio.create_task(self._touch_key(key_h))

        return Project(
            project_id=row["project_id"],
            tier=row["tier"],
            name=row["name"],
            owner_id=row["owner_id"],
            is_active=row["project_active"],
            created_at=row["project_created_at"],
            updated_at=row["project_updated_at"],
        )

    async def _touch_key(self, key_h: str) -> None:
        try:
            pool = self._pool
            if pool is not None:
                await pool.execute(
                    "UPDATE api_keys SET last_used_at = NOW() WHERE key_hash = $1",
                    key_h,
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Project CRUD
    # ------------------------------------------------------------------

    async def create_project(
        self,
        name: str,
        tier: str = "free",
        project_id: str | None = None,
        owner_id: str | None = None,
    ) -> Project:
        pool = self._require_pool()
        pid = project_id.strip() if project_id and project_id.strip() else f"proj_{secrets.token_hex(8)}"
        clean_name = name.strip() or "Untitled Project"
        clean_tier = tier.strip().lower() or "free"

        row = await pool.fetchrow(
            """
            INSERT INTO projects (project_id, name, tier, owner_id, is_active, created_at, updated_at)
            VALUES ($1, $2, $3, $4, TRUE, NOW(), NOW())
            RETURNING project_id, name, tier, owner_id, is_active, created_at, updated_at
            """,
            pid, clean_name, clean_tier, owner_id,
        )
        return Project(
            project_id=row["project_id"],
            tier=row["tier"],
            name=row["name"],
            owner_id=row["owner_id"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_project(self, project_id: str) -> Project | None:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT * FROM projects WHERE project_id = $1",
            project_id,
        )
        if row is None:
            return None
        return Project(
            project_id=row["project_id"],
            tier=row["tier"],
            name=row["name"],
            owner_id=row["owner_id"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def list_projects(self, owner_id: str | None = None) -> list[Project]:
        pool = self._require_pool()
        if owner_id:
            rows = await pool.fetch(
                "SELECT * FROM projects WHERE owner_id = $1 ORDER BY created_at DESC",
                owner_id,
            )
        else:
            rows = await pool.fetch(
                "SELECT * FROM projects ORDER BY created_at DESC"
            )
        return [
            Project(
                project_id=r["project_id"],
                tier=r["tier"],
                name=r["name"],
                owner_id=r["owner_id"],
                is_active=r["is_active"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    async def update_project(
        self,
        project_id: str,
        name: str | None = None,
        tier: str | None = None,
        is_active: bool | None = None,
    ) -> Project | None:
        pool = self._require_pool()
        updates: list[str] = []
        args: list[Any] = []
        idx = 1

        if name is not None:
            updates.append(f"name = ${idx}")
            args.append(name.strip())
            idx += 1
        if tier is not None:
            updates.append(f"tier = ${idx}")
            args.append(tier.strip().lower())
            idx += 1
        if is_active is not None:
            updates.append(f"is_active = ${idx}")
            args.append(is_active)
            idx += 1

        if not updates:
            return await self.get_project(project_id)

        updates.append(f"updated_at = NOW()")
        args.append(project_id)
        sql = f"UPDATE projects SET {', '.join(updates)} WHERE project_id = ${idx} RETURNING *"
        row = await pool.fetchrow(sql, *args)
        if row is None:
            return None
        return Project(
            project_id=row["project_id"],
            tier=row["tier"],
            name=row["name"],
            owner_id=row["owner_id"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def delete_project(self, project_id: str) -> bool:
        pool = self._require_pool()
        res = await pool.execute(
            "DELETE FROM projects WHERE project_id = $1",
            project_id,
        )
        return res.endswith("1")

    # ------------------------------------------------------------------
    # API Key Management
    # ------------------------------------------------------------------

    async def create_key(
        self,
        project_id: str,
        name: str = "Default Key",
        raw_key: str | None = None,
    ) -> tuple[str, APIKeyInfo]:
        """Creates and stores a new API key for the specified project.

        Returns a tuple of (plaintext_raw_key, api_key_info). The plaintext
        key is returned ONLY on creation and must be presented to the user.
        """
        pool = self._require_pool()

        # Check that project exists
        project = await self.get_project(project_id)
        if project is None:
            raise ValueError(f"Project '{project_id}' does not exist")

        plaintext = raw_key or generate_api_key()
        key_h = hash_key(plaintext)
        masked = mask_key(plaintext)
        key_name = name.strip() or "Default Key"

        row = await pool.fetchrow(
            """
            INSERT INTO api_keys (key_hash, project_id, name, prefix, is_active, created_at)
            VALUES ($1, $2, $3, $4, TRUE, NOW())
            RETURNING id, project_id, name, prefix, is_active, created_at, revoked_at, last_used_at
            """,
            key_h, project_id, key_name, masked,
        )

        info = APIKeyInfo(
            id=str(row["id"]),
            project_id=row["project_id"],
            name=row["name"],
            prefix=row["prefix"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
            last_used_at=row["last_used_at"],
        )
        return plaintext, info

    async def list_keys(
        self,
        project_id: str,
        include_revoked: bool = False,
    ) -> list[APIKeyInfo]:
        pool = self._require_pool()
        if include_revoked:
            rows = await pool.fetch(
                """
                SELECT id, project_id, name, prefix, is_active, created_at, revoked_at, last_used_at
                FROM api_keys
                WHERE project_id = $1
                ORDER BY created_at DESC
                """,
                project_id,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT id, project_id, name, prefix, is_active, created_at, revoked_at, last_used_at
                FROM api_keys
                WHERE project_id = $1 AND revoked_at IS NULL AND is_active = TRUE
                ORDER BY created_at DESC
                """,
                project_id,
            )
        return [
            APIKeyInfo(
                id=str(r["id"]),
                project_id=r["project_id"],
                name=r["name"],
                prefix=r["prefix"],
                is_active=r["is_active"],
                created_at=r["created_at"],
                revoked_at=r["revoked_at"],
                last_used_at=r["last_used_at"],
            )
            for r in rows
        ]

    async def get_key_info(
        self,
        project_id: str,
        key_identifier: str,
    ) -> APIKeyInfo | None:
        """Finds key by id (UUID) or prefix."""
        pool = self._require_pool()
        is_uuid = False
        try:
            uuid_val = uuid.UUID(key_identifier)
            is_uuid = True
        except ValueError:
            is_uuid = False

        if is_uuid:
            row = await pool.fetchrow(
                """
                SELECT id, project_id, name, prefix, is_active, created_at, revoked_at, last_used_at
                FROM api_keys
                WHERE project_id = $1 AND id = $2
                """,
                project_id, uuid_val,
            )
        else:
            row = await pool.fetchrow(
                """
                SELECT id, project_id, name, prefix, is_active, created_at, revoked_at, last_used_at
                FROM api_keys
                WHERE project_id = $1 AND prefix = $2
                """,
                project_id, key_identifier,
            )

        if row is None:
            return None

        return APIKeyInfo(
            id=str(row["id"]),
            project_id=row["project_id"],
            name=row["name"],
            prefix=row["prefix"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
            last_used_at=row["last_used_at"],
        )

    async def revoke_key(
        self,
        project_id: str,
        key_identifier: str | None = None,
        key_id: str | None = None,
    ) -> bool:
        """Revokes an API key by setting revoked_at = NOW() and is_active = FALSE."""
        ident = key_id or key_identifier or ""
        pool = self._require_pool()
        is_uuid = False
        try:
            uuid_val = uuid.UUID(ident)
            is_uuid = True
        except ValueError:
            is_uuid = False

        if is_uuid:
            res = await pool.execute(
                """
                UPDATE api_keys
                SET revoked_at = NOW(), is_active = FALSE
                WHERE project_id = $1 AND id = $2 AND revoked_at IS NULL
                """,
                project_id, uuid_val,
            )
        else:
            res = await pool.execute(
                """
                UPDATE api_keys
                SET revoked_at = NOW(), is_active = FALSE
                WHERE project_id = $1 AND prefix = $2 AND revoked_at IS NULL
                """,
                project_id, ident,
            )

        return res.endswith("1")

    async def delete_key(
        self,
        project_id: str,
        key_identifier: str,
    ) -> bool:
        """Permanently deletes an API key."""
        pool = self._require_pool()
        is_uuid = False
        try:
            uuid_val = uuid.UUID(key_identifier)
            is_uuid = True
        except ValueError:
            is_uuid = False

        if is_uuid:
            res = await pool.execute(
                "DELETE FROM api_keys WHERE project_id = $1 AND id = $2",
                project_id, uuid_val,
            )
        else:
            res = await pool.execute(
                "DELETE FROM api_keys WHERE project_id = $1 AND prefix = $2",
                project_id, key_identifier,
            )

        return res.endswith("1")


class InMemoryAPIKeyStore:
    """In-memory key store for unit testing / mocking without Postgres."""

    def __init__(self) -> None:
        self._keys_by_hash: dict[str, Project] = {}
        self._key_infos: dict[str, list[tuple[str, APIKeyInfo]]] = {}  # project_id -> list of (raw_key, info)
        self._projects: dict[str, Project] = {}
        default_proj = Project(project_id="local-dev", tier="free", name="Local Dev Workspace")
        self._projects["local-dev"] = default_proj
        self.register("sk-test-local", default_proj)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def register(self, raw_key: str, project: Project) -> None:
        self._projects[project.project_id] = project
        self._keys_by_hash[hash_key(raw_key)] = project
        info = APIKeyInfo(
            id=str(uuid.uuid4()),
            project_id=project.project_id,
            name="Default Key",
            prefix=mask_key(raw_key),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        self._key_infos.setdefault(project.project_id, []).append((raw_key, info))

    async def resolve(self, api_key: str) -> Project:
        project = self._keys_by_hash.get(hash_key(api_key))
        if project is None:
            raise AuthError("Invalid API key")
        if not project.is_active:
            raise AuthError("Project is suspended or inactive")
        return project

    async def create_project(
        self,
        name: str,
        tier: str = "free",
        project_id: str | None = None,
        owner_id: str | None = None,
    ) -> Project:
        pid = project_id or f"proj_{uuid.uuid4().hex[:8]}"
        proj = Project(
            project_id=pid,
            tier=tier,
            name=name,
            owner_id=owner_id,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        self._projects[pid] = proj
        return proj

    async def get_project(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    async def list_projects(self, owner_id: str | None = None) -> list[Project]:
        if owner_id:
            return [p for p in self._projects.values() if p.owner_id == owner_id]
        return list(self._projects.values())

    async def create_key(
        self,
        project_id: str,
        name: str = "Default Key",
        raw_key: str | None = None,
    ) -> tuple[str, APIKeyInfo]:
        proj = self._projects.get(project_id)
        if not proj:
            raise ValueError(f"Project '{project_id}' does not exist")
        raw = raw_key or generate_api_key()
        info = APIKeyInfo(
            id=str(uuid.uuid4()),
            project_id=project_id,
            name=name,
            prefix=mask_key(raw),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        self._keys_by_hash[hash_key(raw)] = proj
        self._key_infos.setdefault(project_id, []).append((raw, info))
        return raw, info

    async def list_keys(self, project_id: str, include_revoked: bool = False) -> list[APIKeyInfo]:
        items = self._key_infos.get(project_id, [])
        return [info for _, info in items if include_revoked or info.is_active]

    async def revoke_key(self, project_id: str, key_id: str) -> bool:
        items = self._key_infos.get(project_id, [])
        for i, (raw, info) in enumerate(items):
            if info.id == key_id or info.prefix == key_id:
                # Remove from hash map
                self._keys_by_hash.pop(hash_key(raw), None)
                # Mark inactive
                revoked_info = APIKeyInfo(
                    id=info.id,
                    project_id=info.project_id,
                    name=info.name,
                    prefix=info.prefix,
                    is_active=False,
                    created_at=info.created_at,
                    revoked_at=datetime.now(timezone.utc),
                )
                items[i] = (raw, revoked_info)
                return True
        return False
