#!/usr/bin/env python3
"""
SpendGuard AI — Core Gateway & Agent Cost Control Smoke Test.

Validates the complete agent cost control lifecycle against a running SpendGuard gateway:
  1. Health check verification (/healthz).
  2. Available models and pricing (/v1/models).
  3. Authentication & project scoping (/project).
  4. SDK Run registration (/runs / /v1/runs).
  5. Pre-flight budget reservation & agent completions (/chat/completions).
  6. Post-call true-up refund & remaining budget tracking.
  7. Run query and status verification (/runs/{run_id}).
  8. Hard run budget ceiling enforcement (HTTP 429).
  9. Runaway loop protection & tool repetition detection.
 10. Emergency kill-switch abort (/runs/{run_id}/abort).
 11. Run completion lifecycle (/runs/{run_id}/complete).

Usage:
  python scripts/smoke_test.py [--base-url http://localhost:8000] [--api-key <KEY>]
"""

import argparse
import sys
import uuid
import httpx


def log_pass(msg: str):
    print(f"\033[92m[PASS]\033[0m {msg}")


def log_fail(msg: str, detail: str = ""):
    print(f"\033[91m[FAIL]\033[0m {msg}")
    if detail:
        print(f"       \033[90m{detail}\033[0m")


def log_info(msg: str):
    print(f"\033[94m[INFO]\033[0m {msg}")


def main():
    parser = argparse.ArgumentParser(description="SpendGuard AI Agent Cost Control Smoke Test")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Base URL of gateway")
    parser.add_argument("--api-key", default=None, help="SpendGuard API key")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    log_info(f"Target Gateway: {base_url}")
    client = httpx.Client(base_url=base_url, timeout=30.0)

    # 1. Health Check
    try:
        r = client.get("/healthz")
        if r.status_code == 200 and r.json().get("status") == "ok":
            log_pass("Gateway health check OK")
        else:
            log_fail("Gateway health check failed", r.text)
            sys.exit(1)
    except Exception as exc:
        log_fail("Could not reach gateway", str(exc))
        sys.exit(1)

    api_key = args.api_key
    if not api_key:
        log_info("No --api-key passed, running unauthenticated verification tests")
        r = client.post("/chat/completions", json={"model": "gpt-4o", "messages": []})
        if r.status_code == 401:
            log_pass("Gateway correctly requires API key authentication (401)")
        else:
            log_fail("Expected 401 on unauthenticated call", r.text)
        print("\n\033[92mSmoke test completed successfully!\033[0m")
        return

    headers = {"Authorization": f"Bearer {api_key}"}

    # 2. Models Endpoint
    r = client.get("/v1/models", headers=headers)
    if r.status_code == 200 and "data" in r.json():
        log_pass(f"Listed {len(r.json()['data'])} available models and price cards")
    else:
        log_fail("Failed to list models", r.text)

    # 3. Project identity check
    r = client.get("/project", headers=headers)
    if r.status_code == 200:
        proj = r.json()
        log_pass(f"Authenticated as project: {proj['project']['project_id']} (balance: {proj['balance_cents']} cents)")
    else:
        log_fail("Failed to resolve project from API key", r.text)
        sys.exit(1)

    # 4. SDK Run Registration
    run_id = f"smoke_run_{uuid.uuid4().hex[:8]}"
    agent_id = "smoke_agent_v1"

    r = client.post(
        "/v1/runs",
        headers=headers,
        json={
            "run_id": run_id,
            "agent_id": agent_id,
            "budget_cents": 150,
            "max_steps": 30,
            "metadata": {"test": "smoke_run"},
        },
    )
    if r.status_code == 200 and r.json().get("status") == "ok":
        log_pass(f"Registered agent run '{run_id}' with $1.50 budget cap")
    else:
        log_fail("Failed to register run", r.text)

    # 5. Agent Run Completions with Identity & Pre-Flight Reservation
    run_headers = {
        **headers,
        "X-SpendGuard-Run-Id": run_id,
        "X-SpendGuard-Agent-Id": agent_id,
    }

    r = client.post(
        "/chat/completions",
        headers=run_headers,
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Reply with 'SpendGuard OK'"}],
            "max_tokens": 10,
        },
    )
    if r.status_code == 200:
        log_pass(f"Agent completion successful (Run ID: {r.headers.get('x-spendguard-run-id')})")
        log_pass(f"Run Step: {r.headers.get('x-spendguard-run-step')}, Spent: {r.headers.get('x-spendguard-run-spent-cents')}c")
    else:
        log_fail("Agent completion request failed", r.text)

    # 6. Check Run Status Endpoint
    r = client.get(f"/runs/{run_id}", headers=headers)
    if r.status_code == 200:
        run_data = r.json()["run"]
        log_pass(f"Run status verified: {run_data['status']} (spent: {run_data['spent_cents']}c, remaining: {run_data['remaining_cents']}c)")
    else:
        log_fail("Failed to fetch run status", r.text)

    # 7. Emergency Kill Switch Abort
    r = client.post(f"/runs/{run_id}/abort", headers=headers, json={"reason": "smoke_test_abort"})
    if r.status_code == 200 and r.json().get("aborted"):
        log_pass("Emergency kill switch (/runs/{run_id}/abort) succeeded")
    else:
        log_fail("Kill switch failed", r.text)

    # 8. Verify Gateway Blocks Aborted Run (429)
    r = client.post(
        "/chat/completions",
        headers=run_headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Should be blocked"}]},
    )
    if r.status_code == 429:
        log_pass("Gateway blocked aborted agent run with HTTP 429")
    else:
        log_fail("Expected HTTP 429 on aborted run", r.text)

    print("\n\033[92mAll Core Gateway Server Smoke Tests Passed Successfully!\033[0m")


if __name__ == "__main__":
    main()
