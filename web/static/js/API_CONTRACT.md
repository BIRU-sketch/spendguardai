# SpendGuard AI — Frontend API Contract

This document specifies the exact contract implemented by `web/static/js/api.js`.
The frontend UI calls these functions for all dashboard views and the guided onboarding flow.
When transitioning from `USE_MOCK = true` to real backend endpoints, the backend API routes should match these shapes and semantics.

---

## Global Headers & Authentication

For authenticated requests to the backend:
- Header: `Authorization: Bearer <user_session_token_or_api_key>`
- Content-Type: `application/json`

---

## 1. `listKeys()`

Retrieves all API keys associated with the current project or workspace, including revoked keys for auditing.

- **Frontend Call**: `await api.listKeys()`
- **HTTP Equivalent**: `GET /api/keys` (or `GET /projects/{project_id}/keys`)
- **Query Parameters**: None

### Response Shape (`200 OK`)
```json
{
  "keys": [
    {
      "key_id": "key_prod_89a12c",
      "name": "Production Agent Cluster",
      "prefix": "sk-live-7d",
      "last4": "9b2f",
      "full_masked": "sk-live-7d••••••••••••••••9b2f",
      "status": "active", // "active" | "revoked"
      "created_at": "2026-09-29T10:20:00.000Z",
      "last_used_at": "2026-10-13T14:15:00.000Z"
    }
  ],
  "total": 1
}
```

### Errors
- `401 Unauthorized`: Session expired or invalid auth token.
- `500 Internal Error`: Database read failure.

---

## 2. `createKey({ name })`

Generates a new project API key. **The raw secret key must be returned exactly once** in this response; the backend stores only the SHA-256 hash.

- **Frontend Call**: `await api.createKey({ name: "Worker Key" })`
- **HTTP Equivalent**: `POST /api/keys`
- **Request Body**:
```json
{
  "name": "Worker Key"
}
```

### Response Shape (`201 Created` or `200 OK`)
```json
{
  "key": {
    "key_id": "key_f83910c2",
    "name": "Worker Key",
    "prefix": "sk-live-9a",
    "last4": "44bb",
    "full_masked": "sk-live-9a••••••••••••••••44bb",
    "status": "active",
    "created_at": "2026-10-13T14:45:00.000Z",
    "last_used_at": null
  },
  "raw_key": "sk-live-9a84f092eac4910244bb..." // Returned ONCE to client
}
```

### Errors
- `400 Bad Request`: Key name too long or invalid characters.
- `401 Unauthorized`: Authentication required.
- `403 Forbidden`: Project key limit reached on free tier.

---

## 3. `revokeKey(keyId)`

Revokes an API key. Revoked keys are immediately rejected by the gateway but retained in history in a disabled status.

- **Frontend Call**: `await api.revokeKey("key_prod_89a12c")`
- **HTTP Equivalent**: `POST /api/keys/{key_id}/revoke` or `DELETE /api/keys/{key_id}`
- **Path Parameters**: `key_id: string`

### Response Shape (`200 OK`)
```json
{
  "ok": true,
  "key": {
    "key_id": "key_prod_89a12c",
    "status": "revoked",
    "revoked_at": "2026-10-13T14:48:00.000Z"
  }
}
```

### Errors
- `404 Not Found`: Key ID does not belong to the user's project.
- `401 Unauthorized`: Authentication required.

---

## 4. `listRuns({ status, page, limit })`

Retrieves agent runs and trajectories, sorted newest first, with optional status filter and pagination.

- **Frontend Call**: `await api.listRuns({ status: "all", page: 1, limit: 10 })`
- **HTTP Equivalent**: `GET /api/runs?status=all&page=1&limit=10`
- **Query Parameters**:
  - `status`: `"all" | "active" | "completed" | "budget_exceeded" | "runaway_aborted" | "aborted"`
  - `page`: integer (default `1`)
  - `limit`: integer (default `10`)

### Response Shape (`200 OK`)
```json
{
  "runs": [
    {
      "run_id": "run_active_4091a",
      "agent_id": "web-researcher-agent",
      "session_id": "sess_user_9921",
      "model": "gpt-4o",
      "status": "active", // "active" | "completed" | "budget_exceeded" | "runaway_aborted" | "aborted"
      "spent_cents": 42,
      "spent_usd": 0.42,
      "budget_cents": 200,
      "budget_usd": 2.00,
      "remaining_cents": 158,
      "remaining_usd": 1.58,
      "step_count": 8,
      "max_steps": 30,
      "started_at": "2026-10-13T14:30:00.000Z",
      "updated_at": "2026-10-13T14:34:00.000Z",
      "metadata": {
        "environment": "production"
      },
      "tripwire_reason": null
    }
  ],
  "total": 5,
  "page": 1,
  "limit": 10,
  "total_pages": 1
}
```

### Errors
- `400 Bad Request`: Invalid status filter or page number.
- `401 Unauthorized`: Session missing or expired.

---

## 5. `getRun(runId)`

Retrieves granular trajectory logs, step records, and reason strings for a specific run.

- **Frontend Call**: `await api.getRun("run_loop_8820c")`
- **HTTP Equivalent**: `GET /api/runs/{run_id}`
- **Path Parameters**: `run_id: string`

### Response Shape (`200 OK`)
```json
{
  "run": {
    "run_id": "run_loop_8820c",
    "agent_id": "sql-crawler-agent",
    "session_id": "sess_etl_661",
    "model": "gpt-4o-mini",
    "status": "runaway_aborted",
    "spent_cents": 85,
    "spent_usd": 0.85,
    "budget_cents": 150,
    "budget_usd": 1.50,
    "remaining_cents": 65,
    "remaining_usd": 0.65,
    "step_count": 16,
    "max_steps": 25,
    "started_at": "2026-10-13T12:00:00.000Z",
    "updated_at": "2026-10-13T12:03:00.000Z",
    "tripwire_reason": "repeated_tool_calls: 'execute_sql' called 4 consecutive times with identical parameters",
    "execution_log": [
      {
        "step": 1,
        "timestamp": "2026-10-13T12:00:00.000Z",
        "event": "pre_flight_reserve",
        "amount_cents": 12,
        "balance_check": "passed"
      },
      {
        "step": 16,
        "timestamp": "2026-10-13T12:03:00.000Z",
        "event": "runaway_aborted",
        "details": "repeated_tool_calls: 'execute_sql' called 4 consecutive times with identical parameters"
      }
    ]
  }
}
```

### Errors
- `404 Not Found`: Run does not exist.

---

## 6. `abortRun(runId, reason)`

Operator Emergency Kill-Switch. Aborts an active agent run immediately in Redis and PostgreSQL. Upstream token generation is halted.

- **Frontend Call**: `await api.abortRun("run_active_4091a", "operator_manual_kill")`
- **HTTP Equivalent**: `POST /api/runs/{run_id}/abort`
- **Request Body**:
```json
{
  "reason": "operator_manual_kill"
}
```

### Response Shape (`200 OK`)
```json
{
  "ok": true,
  "run": {
    "run_id": "run_active_4091a",
    "status": "aborted",
    "updated_at": "2026-10-13T14:49:00.000Z"
  }
}
```

---

## 7. `getUsage({ period })`

Retrieves aggregated spend, requests count, run status breakdown, and daily spend arrays for charting.

- **Frontend Call**: `await api.getUsage({ period: "7d" })`
- **HTTP Equivalent**: `GET /api/usage?period=7d` (or `?period=month`)
- **Query Parameters**: `period: "7d" | "month"`

### Response Shape (`200 OK`)
```json
{
  "period": "7d",
  "total_spend_usd": 3.07,
  "total_requests": 53,
  "quota_remaining_usd": 21.93,
  "runs_by_status": {
    "completed": 1,
    "active": 1,
    "budget_exceeded": 1,
    "runaway_aborted": 1,
    "aborted": 1
  },
  "daily_spend": [
    { "day": "Mon", "date": "Oct 07", "spend_usd": 0.28, "requests": 5 },
    { "day": "Tue", "date": "Oct 08", "spend_usd": 0.65, "requests": 11 },
    { "day": "Wed", "date": "Oct 09", "spend_usd": 0.18, "requests": 4 },
    { "day": "Thu", "date": "Oct 10", "spend_usd": 0.85, "requests": 14 },
    { "day": "Fri", "date": "Oct 11", "spend_usd": 0.42, "requests": 7 },
    { "day": "Sat", "date": "Oct 12", "spend_usd": 0.15, "requests": 3 },
    { "day": "Sun", "date": "Oct 13", "spend_usd": 0.54, "requests": 9 }
  ]
}
```

---

## 8. `runTestCall({ apiKey, model, budgetUsd, maxSteps })`

Dispatches a simulated protected request through the SpendGuard gateway layer. Used by Onboarding Step 3 to demonstrate how SpendGuard blocks an LLM call when the hard limit is tripped, resulting in HTTP 429 and zero upstream billing.

- **Frontend Call**: `await api.runTestCall({ budgetUsd: 1.50, maxSteps: 25 })`
- **HTTP Equivalent**: Simulated Gateway Call (`POST /v1/chat/completions`)

### Response Shape
```json
{
  "simulated": true,
  "status": 429,
  "statusText": "Too Many Requests",
  "headers": {
    "X-SpendGuard-Tripwire": "budget_exceeded",
    "X-SpendGuard-Request-Cost-Cents": "0",
    "X-SpendGuard-Run-Spent-Cents": "150",
    "X-SpendGuard-Run-Remaining-Cents": "0",
    "X-SpendGuard-Run-Step": "12",
    "X-SpendGuard-Run-Id": "run_sim_8923a1"
  },
  "body": {
    "error": {
      "message": "Execution blocked: Run 'run_sim_8923a1' exhausted budget limit ($1.50 USD). 0 upstream tokens billed.",
      "type": "budget_exceeded",
      "param": "budget_cents",
      "code": 429,
      "details": {
        "run_id": "run_sim_8923a1",
        "budget_cents": 150,
        "spent_cents": 150,
        "upstream_billed_cents": 0
      }
    }
  },
  "equivalent_script": {
    "curl": "curl https://api.spendguard.ai/v1/chat/completions \\\n  -H \"Authorization: Bearer sk-live-...\" ..."
  }
}
```

---

## 9. `getOnboardingState()` & `setOnboardingStep(stepNumber, value)`

Manages the 3-step onboarding checklist persistence.

- **Checklist Structure**:
```json
{
  "step1_created_key": true,
  "step2_viewed_code": true,
  "step3_tested_call": true,
  "completed": true,
  "last_created_key": "sk-live-..." // or null if dismissed
}
```
