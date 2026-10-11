import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, make_response
from dotenv import load_dotenv
import redis

# Running `python web/web_server.py` puts web/ (not the repository root) on
# sys.path. Add the root before importing app modules, regardless of cwd.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from supabase import create_client, Client
from app.auth import PostgresAPIKeyStore, Project, APIKeyInfo
from app.config import config
from app.budget import AgentBudgetManager

supabase: Client = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", ""))
app = Flask(__name__)


def _run_async(coro):
    """Utility to run an async database coroutine synchronously in Flask."""
    return asyncio.run(coro)


async def _get_store() -> PostgresAPIKeyStore:
    store = PostgresAPIKeyStore(config.database_url)
    await store.start()
    return store


async def _get_budget_manager() -> AgentBudgetManager:
    manager = AgentBudgetManager(config.database_url, config.redis_url)
    await manager.start()
    return manager


def _get_authenticated_user():
    """Extracts and verifies the current Supabase user from the access_token cookie."""
    access_token = request.cookies.get("access_token")
    if not access_token:
        return None
    try:
        user_response = supabase.auth.get_user(access_token)
        return user_response.user if user_response else None
    except Exception:
        app.logger.exception("Failed to get authenticated Supabase user")
        return None


def _get_project_balance(project_id: str, tier: str = "free") -> int:
    """Reads project balance from Redis in USD cents."""
    try:
        r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
        key = f"project:{project_id}:balance_cents"
        val = r.get(key)
        if val is None:
            tier_default = 1000  # $10.00
            if tier == "builder":
                tier_default = 5000
            elif tier == "team":
                tier_default = 20000
            r.set(key, tier_default)
            return tier_default
        return int(val)
    except Exception:
        return 1000


def _topup_project_balance(project_id: str, amount_cents: int = 1000, reset: bool = False) -> int:
    """Sets or increments project balance in Redis."""
    r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    key = f"project:{project_id}:balance_cents"
    if reset:
        r.set(key, amount_cents)
        return amount_cents
    curr = r.get(key)
    new_bal = max(int(curr or 0), 0) + amount_cents
    r.set(key, new_bal)
    return new_bal


async def _ensure_user_project(owner_id: str, owner_name: str, email: str) -> tuple[list[Project], Project, str | None]:
    """Ensures the user has at least one project and returns (all_projects, active_project, initial_key)."""
    store = await _get_store()
    try:
        projs = await store.list_projects(owner_id=owner_id)
        initial_key = None
        if not projs:
            clean_name = owner_name if owner_name and owner_name != "user" else (email.split("@")[0] if email else "My Project")
            workspace_name = f"{clean_name}'s Workspace"
            active_proj = await store.create_project(
                name=workspace_name,
                tier="free",
                owner_id=owner_id,
            )
            raw_key, _ = await store.create_key(active_proj.project_id, name="Default Key")
            initial_key = raw_key
            _topup_project_balance(active_proj.project_id, 1000, reset=True)
            projs = [active_proj]
        else:
            active_proj = projs[0]
            _get_project_balance(active_proj.project_id, active_proj.tier)
        return projs, active_proj, initial_key
    finally:
        await store.stop()


# ---------------------------------------------------------------------------
# Page Routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/dashboard")
def dashboard():
    user = _get_authenticated_user()
    if not user:
        return redirect(url_for("auth"))

    user_name = user.user_metadata.get("name") or (user.email.split("@")[0] if user.email else "Developer")
    user_email = user.email or ""

    try:
        all_projects, default_active, initial_key = _run_async(
            _ensure_user_project(user.id, user_name, user_email)
        )
    except Exception:
        app.logger.exception("Error ensuring user project")
        return redirect(url_for("auth"))

    requested_pid = request.args.get("project_id")
    active_project = default_active
    if requested_pid:
        for p in all_projects:
            if p.project_id == requested_pid:
                active_project = p
                break

    balance_cents = _get_project_balance(active_project.project_id, active_project.tier)
    balance_formatted = f"${balance_cents / 100:,.2f}"

    async def _fetch_keys():
        store = await _get_store()
        try:
            return await store.list_keys(active_project.project_id, include_revoked=True)
        finally:
            await store.stop()

    try:
        keys = _run_async(_fetch_keys())
    except Exception:
        app.logger.exception("Error listing API keys for project %s", active_project.project_id)
        keys = []

    return render_template(
        "dashboard.html",
        user={"id": user.id, "name": user_name, "email": user_email},
        all_projects=all_projects,
        active_project=active_project,
        keys=keys,
        plans=[],
        wallet=None,
        ledger_entries=[],
        payout_account=None,
        balance_formatted=balance_formatted,
        initial_key=initial_key,
    )


@app.route("/auth")
def auth():
    user = _get_authenticated_user()
    if user:
        return redirect(url_for("dashboard"))
    return render_template("auth.html")


@app.route("/auth/signin", methods=["POST"])
def signin():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    try:
        auth_response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if auth_response.session:
            resp = make_response(jsonify({"status": "ok", "redirect": "/dashboard"}))
            resp.set_cookie("access_token", auth_response.session.access_token, httponly=True, samesite="Lax")
            return resp
        return jsonify({"error": "Invalid credentials"}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/auth/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    password = data.get("password")
    name = data.get("name")

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    try:
        signup_data = {"email": email, "password": password}
        if name:
            signup_data["options"] = {"data": {"name": name}}
        auth_response = supabase.auth.sign_up(signup_data)
        if auth_response.session:
            resp = make_response(jsonify({"status": "ok", "redirect": "/dashboard"}))
            resp.set_cookie("access_token", auth_response.session.access_token, httponly=True, samesite="Lax")
            return resp
        return jsonify({"status": "ok", "message": "Check your email for confirmation."})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/auth/signout", methods=["GET", "POST"])
def signout():
    resp = make_response(redirect(url_for("auth")))
    resp.delete_cookie("access_token")
    return resp


# ---------------------------------------------------------------------------
# API Key Endpoints
# ---------------------------------------------------------------------------

@app.route("/api/keys", methods=["POST"])
def create_key_api():
    user = _get_authenticated_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id")
    name = data.get("name", "Default Key")

    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    async def _create():
        store = await _get_store()
        try:
            proj = await store.get_project(project_id)
            if not proj:
                return None, None, "Project not found", 404
            if proj.owner_id and proj.owner_id != user.id:
                return None, None, "Forbidden", 403
            key_info, raw_key = await store.create_key(project_id=project_id, name=name)
            return key_info, raw_key, None, 200
        finally:
            await store.stop()

    try:
        key_info, raw_key, err_msg, status_code = _run_async(_create())
        if err_msg:
            return jsonify({"error": err_msg}), status_code
        return jsonify({
            "status": "ok",
            "api_key": {
                "id": str(key_info.id),
                "name": key_info.name,
                "prefix": key_info.prefix,
                "created_at": key_info.created_at.isoformat() if key_info.created_at else None,
            },
            "raw_key": raw_key,
        })
    except Exception as exc:
        app.logger.exception("Failed to create API key")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/keys/<key_id>/revoke", methods=["POST", "DELETE"])
def revoke_key_api(key_id: str):
    user = _get_authenticated_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id")

    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    async def _revoke():
        store = await _get_store()
        try:
            proj = await store.get_project(project_id)
            if not proj:
                return False, "Project not found", 404
            if proj.owner_id and proj.owner_id != user.id:
                return False, "Forbidden", 403
            ok = await store.revoke_key(project_id=project_id, key_id=key_id)
            if not ok:
                return False, "Key not found", 404
            return True, None, 200
        finally:
            await store.stop()

    try:
        ok, err_msg, status_code = _run_async(_revoke())
        if err_msg:
            return jsonify({"error": err_msg}), status_code
        return jsonify({"status": "ok", "revoked": True})
    except Exception as exc:
        app.logger.exception("Failed to revoke API key")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/projects", methods=["POST"])
def create_project_api():
    user = _get_authenticated_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    name = data.get("name", "New Project")
    tier = data.get("tier", "free")

    async def _create_proj():
        store = await _get_store()
        try:
            proj = await store.create_project(name=name, tier=tier, owner_id=user.id)
            key_info, raw_key = await store.create_key(proj.project_id, name="Default Key")
            _topup_project_balance(proj.project_id, 1000, reset=True)
            return proj, raw_key, None, 200
        finally:
            await store.stop()

    try:
        proj, raw_key, err_msg, status_code = _run_async(_create_proj())
        if err_msg:
            return jsonify({"error": err_msg}), status_code
        return jsonify({
            "status": "ok",
            "project": {
                "project_id": proj.project_id,
                "name": proj.name,
                "tier": proj.tier,
                "created_at": proj.created_at.isoformat() if proj.created_at else None,
            },
            "raw_key": raw_key,
        })
    except Exception as exc:
        app.logger.exception("Failed to create project")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/wallets/topup", methods=["POST"])
def wallet_topup_api():
    user = _get_authenticated_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id")
    amount_cents = data.get("amount_cents")

    if not project_id or not amount_cents:
        return jsonify({"error": "project_id and amount_cents are required"}), 400

    try:
        cents = int(amount_cents)
        if cents <= 0:
            return jsonify({"error": "amount_cents must be positive"}), 400
    except ValueError:
        return jsonify({"error": "amount_cents must be an integer"}), 400

    async def _topup():
        store = await _get_store()
        budget = await _get_budget_manager()
        try:
            proj = await store.get_project(project_id)
            if not proj:
                return None, "Project not found", 404
            if proj.owner_id and proj.owner_id != user.id:
                return None, "Forbidden", 403

            new_bal = await budget.topup_project_balance(project_id, cents)
            return {"balance_cents": new_bal}, None, 200
        finally:
            await asyncio.gather(store.stop(), budget.stop(), return_exceptions=True)

    try:
        res, err_msg, status_code = _run_async(_topup())
        if err_msg:
            return jsonify({"error": err_msg}), status_code
        return jsonify({"status": "ok", "wallet": res})
    except Exception as exc:
        app.logger.exception("Failed to top-up wallet")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
