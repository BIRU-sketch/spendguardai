/**
 * SpendGuard AI — Frontend Mock Data Layer & API Client
 *
 * Provides typed async methods for dashboard & onboarding interactions.
 * In mock mode (USE_MOCK = true), returns realistic synthetic data with
 * simulated network delays (300-600ms) and persists client mutations to localStorage.
 */

// ============================================================================
// Configuration & URL State Flags
// ============================================================================
export const USE_MOCK = true;

const urlParams = new URLSearchParams(window.location.search);
export const FORCE_EMPTY = urlParams.get("empty") === "1";
export const FORCE_ERROR = urlParams.get("error") === "1";

const STORAGE_KEYS_KEY = "sg_mock_keys_v2";
const STORAGE_RUNS_KEY = "sg_mock_runs_v2";
const STORAGE_ONBOARDING_KEY = "sg_mock_onboarding_v2";

// ============================================================================
// Helper Utilities
// ============================================================================
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomDelay() {
  return sleep(300 + Math.floor(Math.random() * 300));
}

function generateRandomHex(length = 32) {
  const chars = "abcdef0123456789";
  let result = "";
  for (let i = 0; i < length; i++) {
    result += chars[Math.floor(Math.random() * chars.length)];
  }
  return result;
}

function generateRawApiKey() {
  return `sk-live-${generateRandomHex(24)}`;
}

function checkForceError(operationName) {
  if (FORCE_ERROR) {
    const error = new Error(`Simulated mock error for operation: ${operationName}`);
    error.status = 500;
    error.code = "mock_simulated_error";
    throw error;
  }
}

// ============================================================================
// Seed Data Initialization
// ============================================================================
function getInitialKeys() {
  if (FORCE_EMPTY) return [];

  const saved = localStorage.getItem(STORAGE_KEYS_KEY);
  if (saved) {
    try {
      return JSON.parse(saved);
    } catch (e) {
      // Fall through to initial seed
    }
  }

  const defaultKeys = [
    {
      key_id: "key_prod_89a12c",
      name: "Production Agent Cluster",
      prefix: "sk-live-7d",
      last4: "9b2f",
      full_masked: "sk-live-7d••••••••••••••••9b2f",
      status: "active",
      created_at: new Date(Date.now() - 14 * 86400000).toISOString(),
      last_used_at: new Date(Date.now() - 12 * 60000).toISOString(),
    },
    {
      key_id: "key_dev_44e08b",
      name: "Local Staging Worker",
      prefix: "sk-live-3a",
      last4: "11de",
      full_masked: "sk-live-3a••••••••••••••••11de",
      status: "revoked",
      created_at: new Date(Date.now() - 28 * 86400000).toISOString(),
      last_used_at: new Date(Date.now() - 5 * 86400000).toISOString(),
    },
  ];
  localStorage.setItem(STORAGE_KEYS_KEY, JSON.stringify(defaultKeys));
  return defaultKeys;
}

function getInitialRuns() {
  if (FORCE_EMPTY) return [];

  const saved = localStorage.getItem(STORAGE_RUNS_KEY);
  if (saved) {
    try {
      return JSON.parse(saved);
    } catch (e) {
      // Fall through to initial seed
    }
  }

  const now = Date.now();
  const defaultRuns = [
    {
      run_id: "run_active_4091a",
      agent_id: "web-researcher-agent",
      session_id: "sess_user_9921",
      model: "gpt-4o",
      status: "active",
      spent_cents: 42,
      spent_usd: 0.42,
      budget_cents: 200,
      budget_usd: 2.00,
      remaining_cents: 158,
      remaining_usd: 1.58,
      step_count: 8,
      max_steps: 30,
      started_at: new Date(now - 4 * 60000).toISOString(),
      updated_at: new Date(now - 1 * 60000).toISOString(),
      metadata: { environment: "production", task: "market_analysis" },
      tripwire_reason: null,
    },
    {
      run_id: "run_comp_9231f",
      agent_id: "code-reviewer-bot",
      session_id: "sess_pr_784",
      model: "claude-3-5-sonnet",
      status: "completed",
      spent_cents: 18,
      spent_usd: 0.18,
      budget_cents: 100,
      budget_usd: 1.00,
      remaining_cents: 82,
      remaining_usd: 0.82,
      step_count: 3,
      max_steps: 10,
      started_at: new Date(now - 42 * 60000).toISOString(),
      updated_at: new Date(now - 38 * 60000).toISOString(),
      metadata: { pr_number: 142, repo: "backend-core" },
      tripwire_reason: null,
    },
    {
      run_id: "run_loop_8820c",
      agent_id: "sql-crawler-agent",
      session_id: "sess_etl_661",
      model: "gpt-4o-mini",
      status: "runaway_aborted",
      spent_cents: 85,
      spent_usd: 0.85,
      budget_cents: 150,
      budget_usd: 1.50,
      remaining_cents: 65,
      remaining_usd: 0.65,
      step_count: 16,
      max_steps: 25,
      started_at: new Date(now - 140 * 60000).toISOString(),
      updated_at: new Date(now - 138 * 60000).toISOString(),
      metadata: { target_table: "analytics_events" },
      tripwire_reason: "repeated_tool_calls: 'execute_sql' called 4 consecutive times with identical parameters",
    },
    {
      run_id: "run_cap_3108d",
      agent_id: "document-parser-v2",
      session_id: "sess_batch_12",
      model: "gpt-4o",
      status: "budget_exceeded",
      spent_cents: 150,
      spent_usd: 1.50,
      budget_cents: 150,
      budget_usd: 1.50,
      remaining_cents: 0,
      remaining_usd: 0.00,
      step_count: 24,
      max_steps: 25,
      started_at: new Date(now - 320 * 60000).toISOString(),
      updated_at: new Date(now - 312 * 60000).toISOString(),
      metadata: { file_count: 18 },
      tripwire_reason: "budget_ceiling_reached: Trajectory spend reached exact hard limit ($1.50 USD). 0 upstream tokens billed post-cap.",
    },
    {
      run_id: "run_abort_1194e",
      agent_id: "lead-qualifier",
      session_id: "sess_crm_44",
      model: "claude-3-5-sonnet",
      status: "aborted",
      spent_cents: 12,
      spent_usd: 0.12,
      budget_cents: 300,
      budget_usd: 3.00,
      remaining_cents: 288,
      remaining_usd: 2.88,
      step_count: 2,
      max_steps: 50,
      started_at: new Date(now - 600 * 60000).toISOString(),
      updated_at: new Date(now - 598 * 60000).toISOString(),
      metadata: { trigger: "operator_halt" },
      tripwire_reason: "operator_kill_switch: Emergency halt triggered via dashboard",
    },
  ];
  localStorage.setItem(STORAGE_RUNS_KEY, JSON.stringify(defaultRuns));
  return defaultRuns;
}

function getInitialOnboardingState() {
  const keys = getInitialKeys();
  const saved = localStorage.getItem(STORAGE_ONBOARDING_KEY);
  if (saved) {
    try {
      return JSON.parse(saved);
    } catch (e) {}
  }

  // If there are keys, mark step 1 done by default
  const hasKeys = keys.length > 0;
  const initial = {
    step1_created_key: hasKeys,
    step2_viewed_code: false,
    step3_tested_call: false,
    completed: false,
    last_created_key: null,
  };
  localStorage.setItem(STORAGE_ONBOARDING_KEY, JSON.stringify(initial));
  return initial;
}

// ============================================================================
// Public Mock API Functions
// ============================================================================

/**
 * List all API keys for the current project.
 */
export async function listKeys() {
  await randomDelay();
  checkForceError("listKeys");
  const keys = getInitialKeys();
  return {
    keys: [...keys],
    total: keys.length,
  };
}

/**
 * Generate a new API key.
 * Returns the raw secret key ONCE.
 */
export async function createKey({ name = "Default Key" } = {}) {
  await randomDelay();
  checkForceError("createKey");

  const rawKey = generateRawApiKey();
  const prefix = rawKey.slice(0, 10);
  const last4 = rawKey.slice(-4);
  const keyId = `key_${generateRandomHex(8)}`;

  const newKeyRecord = {
    key_id: keyId,
    name: name.trim() || "Default Key",
    prefix,
    last4,
    full_masked: `${prefix}••••••••••••••••${last4}`,
    status: "active",
    created_at: new Date().toISOString(),
    last_used_at: null,
  };

  const keys = getInitialKeys();
  keys.unshift(newKeyRecord);
  localStorage.setItem(STORAGE_KEYS_KEY, JSON.stringify(keys));

  // Update onboarding state
  const onboarding = getInitialOnboardingState();
  onboarding.step1_created_key = true;
  onboarding.last_created_key = rawKey;
  localStorage.setItem(STORAGE_ONBOARDING_KEY, JSON.stringify(onboarding));

  return {
    key: newKeyRecord,
    raw_key: rawKey,
  };
}

/**
 * Revoke an existing API key.
 */
export async function revokeKey(keyId) {
  await randomDelay();
  checkForceError("revokeKey");

  const keys = getInitialKeys();
  const keyIndex = keys.findIndex((k) => k.key_id === keyId);
  if (keyIndex === -1) {
    const error = new Error(`API key '${keyId}' not found`);
    error.status = 404;
    throw error;
  }

  keys[keyIndex] = {
    ...keys[keyIndex],
    status: "revoked",
    revoked_at: new Date().toISOString(),
  };
  localStorage.setItem(STORAGE_KEYS_KEY, JSON.stringify(keys));

  return {
    ok: true,
    key: keys[keyIndex],
  };
}

/**
 * List agent runs with optional status filtering & pagination.
 */
export async function listRuns({ status = "all", page = 1, limit = 10 } = {}) {
  await randomDelay();
  checkForceError("listRuns");

  let runs = getInitialRuns();

  if (status && status !== "all") {
    runs = runs.filter((r) => r.status === status);
  }

  // Newest first sorting
  runs.sort((a, b) => new Date(b.started_at) - new Date(a.started_at));

  const total = runs.length;
  const startIndex = (page - 1) * limit;
  const paginatedRuns = runs.slice(startIndex, startIndex + limit);

  return {
    runs: paginatedRuns,
    total,
    page,
    limit,
    total_pages: Math.max(1, Math.ceil(total / limit)),
  };
}

/**
 * Retrieve granular details for a single agent run.
 */
export async function getRun(runId) {
  await randomDelay();
  checkForceError("getRun");

  const runs = getInitialRuns();
  const run = runs.find((r) => r.run_id === runId);
  if (!run) {
    const error = new Error(`Run trajectory '${runId}' not found`);
    error.status = 404;
    throw error;
  }

  return {
    run: {
      ...run,
      execution_log: [
        {
          step: 1,
          timestamp: run.started_at,
          event: "pre_flight_reserve",
          amount_cents: 12,
          balance_check: "passed",
        },
        {
          step: run.step_count,
          timestamp: run.updated_at,
          event: run.status === "active" ? "heartbeat" : run.status,
          details: run.tripwire_reason || "Executing within budget parameters",
        },
      ],
    },
  };
}

/**
 * Abort an active agent run (Emergency Kill-Switch).
 */
export async function abortRun(runId, reason = "operator_manual_kill") {
  await randomDelay();
  checkForceError("abortRun");

  const runs = getInitialRuns();
  const runIndex = runs.findIndex((r) => r.run_id === runId);
  if (runIndex === -1) {
    const error = new Error(`Run trajectory '${runId}' not found`);
    error.status = 404;
    throw error;
  }

  runs[runIndex] = {
    ...runs[runIndex],
    status: "aborted",
    updated_at: new Date().toISOString(),
    tripwire_reason: `operator_kill_switch: ${reason}`,
  };
  localStorage.setItem(STORAGE_RUNS_KEY, JSON.stringify(runs));

  return {
    ok: true,
    run: runs[runIndex],
  };
}

/**
 * Retrieve aggregated usage statistics for 7 days or this month.
 */
export async function getUsage({ period = "7d" } = {}) {
  await randomDelay();
  checkForceError("getUsage");

  if (FORCE_EMPTY) {
    return {
      period,
      total_spend_usd: 0.00,
      total_requests: 0,
      quota_remaining_usd: 25.00,
      runs_by_status: {
        completed: 0,
        active: 0,
        budget_exceeded: 0,
        runaway_aborted: 0,
        aborted: 0,
      },
      daily_spend: [
        { day: "Mon", date: "Oct 07", spend_usd: 0.00, requests: 0 },
        { day: "Tue", date: "Oct 08", spend_usd: 0.00, requests: 0 },
        { day: "Wed", date: "Oct 09", spend_usd: 0.00, requests: 0 },
        { day: "Thu", date: "Oct 10", spend_usd: 0.00, requests: 0 },
        { day: "Fri", date: "Oct 11", spend_usd: 0.00, requests: 0 },
        { day: "Sat", date: "Oct 12", spend_usd: 0.00, requests: 0 },
        { day: "Sun", date: "Oct 13", spend_usd: 0.00, requests: 0 },
      ],
    };
  }

  const runs = getInitialRuns();
  const runsCount = {
    completed: runs.filter((r) => r.status === "completed").length,
    active: runs.filter((r) => r.status === "active").length,
    budget_exceeded: runs.filter((r) => r.status === "budget_exceeded").length,
    runaway_aborted: runs.filter((r) => r.status === "runaway_aborted").length,
    aborted: runs.filter((r) => r.status === "aborted").length,
  };

  const isMonth = period === "month";
  const totalSpend = isMonth ? 14.85 : 3.07;
  const totalRequests = isMonth ? 218 : 53;
  const quotaRemaining = isMonth ? 35.15 : 21.93;

  const dailySpend = isMonth
    ? [
        { day: "W1", date: "Sep 15 - Sep 21", spend_usd: 3.20, requests: 48 },
        { day: "W2", date: "Sep 22 - Sep 28", spend_usd: 4.15, requests: 62 },
        { day: "W3", date: "Sep 29 - Oct 05", spend_usd: 4.43, requests: 65 },
        { day: "W4", date: "Oct 06 - Oct 13", spend_usd: 3.07, requests: 43 },
      ]
    : [
        { day: "Mon", date: "Oct 07", spend_usd: 0.28, requests: 5 },
        { day: "Tue", date: "Oct 08", spend_usd: 0.65, requests: 11 },
        { day: "Wed", date: "Oct 09", spend_usd: 0.18, requests: 4 },
        { day: "Thu", date: "Oct 10", spend_usd: 0.85, requests: 14 },
        { day: "Fri", date: "Oct 11", spend_usd: 0.42, requests: 7 },
        { day: "Sat", date: "Oct 12", spend_usd: 0.15, requests: 3 },
        { day: "Sun", date: "Oct 13", spend_usd: 0.54, requests: 9 },
      ];

  return {
    period,
    total_spend_usd: totalSpend,
    total_requests: totalRequests,
    quota_remaining_usd: quotaRemaining,
    runs_by_status: runsCount,
    daily_spend: dailySpend,
  };
}

/**
 * Execute a simulated protected LLM call.
 * Emulates a real agent trajectory reaching its hard budget cap ($1.50).
 * Returns simulated HTTP 429 response, $0.00 upstream billing, and header telemetry.
 */
export async function runTestCall({
  apiKey = "sk-live-simulated-key",
  model = "gpt-4o",
  budgetUsd = 1.50,
  maxSteps = 25,
} = {}) {
  await sleep(550);
  checkForceError("runTestCall");

  const runId = `run_sim_${generateRandomHex(6)}`;
  const budgetCents = Math.round(budgetUsd * 100);

  // Add the simulated blocked run to mock history
  const runs = getInitialRuns();
  const simulatedRun = {
    run_id: runId,
    agent_id: "quickstart-agent",
    session_id: "sess_onboarding_demo",
    model,
    status: "budget_exceeded",
    spent_cents: budgetCents,
    spent_usd: budgetUsd,
    budget_cents: budgetCents,
    budget_usd: budgetUsd,
    remaining_cents: 0,
    remaining_usd: 0.00,
    step_count: 12,
    max_steps: maxSteps,
    started_at: new Date(Date.now() - 30000).toISOString(),
    updated_at: new Date().toISOString(),
    metadata: { demo: true, simulated: true },
    tripwire_reason: `budget_ceiling_reached: Trajectory spend reached exact limit ($${budgetUsd.toFixed(2)} USD). Zero upstream tokens billed.`,
  };
  runs.unshift(simulatedRun);
  localStorage.setItem(STORAGE_RUNS_KEY, JSON.stringify(runs));

  // Mark step 3 in onboarding completed
  const onboarding = getInitialOnboardingState();
  onboarding.step3_tested_call = true;
  onboarding.completed = true;
  localStorage.setItem(STORAGE_ONBOARDING_KEY, JSON.stringify(onboarding));

  return {
    simulated: true,
    status: 429,
    statusText: "Too Many Requests",
    headers: {
      "X-SpendGuard-Tripwire": "budget_exceeded",
      "X-SpendGuard-Request-Cost-Cents": "0",
      "X-SpendGuard-Run-Spent-Cents": String(budgetCents),
      "X-SpendGuard-Run-Remaining-Cents": "0",
      "X-SpendGuard-Run-Step": "12",
      "X-SpendGuard-Run-Id": runId,
    },
    body: {
      error: {
        message: `Execution blocked: Run '${runId}' exhausted budget limit ($${budgetUsd.toFixed(2)} USD). 0 upstream tokens billed.`,
        type: "budget_exceeded",
        param: "budget_cents",
        code: 429,
        details: {
          run_id: runId,
          budget_cents: budgetCents,
          spent_cents: budgetCents,
          upstream_billed_cents: 0,
        },
      },
    },
    equivalent_script: {
      curl: `curl https://api.spendguard.ai/v1/chat/completions \\\n  -H "Authorization: Bearer ${apiKey}" \\\n  -H "Content-Type: application/json" \\\n  -H "X-SpendGuard-Agent-Id: quickstart-agent" \\\n  -H "X-SpendGuard-Run-Budget-Cents: ${budgetCents}" \\\n  -H "X-SpendGuard-Max-Steps: ${maxSteps}" \\\n  -d '{\n    "model": "${model}",\n    "messages": [{"role": "user", "content": "Trigger test call"}]\n  }'`,
    },
  };
}

/**
 * Retrieve onboarding state checklist.
 */
export async function getOnboardingState() {
  await sleep(150);
  checkForceError("getOnboardingState");
  return getInitialOnboardingState();
}

/**
 * Update onboarding checklist step.
 */
export async function setOnboardingStep(stepNumber, value = true) {
  const onboarding = getInitialOnboardingState();
  if (stepNumber === 1) onboarding.step1_created_key = value;
  if (stepNumber === 2) onboarding.step2_viewed_code = value;
  if (stepNumber === 3) onboarding.step3_tested_call = value;

  onboarding.completed =
    onboarding.step1_created_key &&
    onboarding.step2_viewed_code &&
    onboarding.step3_tested_call;

  localStorage.setItem(STORAGE_ONBOARDING_KEY, JSON.stringify(onboarding));
  return onboarding;
}

/**
 * Clear mock state and restore defaults.
 */
export function resetMockData() {
  localStorage.removeItem(STORAGE_KEYS_KEY);
  localStorage.removeItem(STORAGE_RUNS_KEY);
  localStorage.removeItem(STORAGE_ONBOARDING_KEY);
  window.location.search = "";
}
