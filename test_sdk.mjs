import {
  SpendGuard,
  wrapOpenAI,
  createSpendGuardFetch,
  spendguardFetch,
  BudgetExceededError,
  RunawayLoopError,
  RunAbortedError,
  AuthenticationError,
  parseSpendGuardError,
  extractTelemetryFromHeaders,
} from "./packages/sdk/dist/esm/index.js";

console.log("=== Testing sg-run ===");

// 1. Error hierarchy tests
const bErr = parseSpendGuardError(429, {
  error: {
    message: "Run budget exhausted",
    scope: "run_budget",
    reason: "run_budget_exhausted",
    run_id: "run-test-1",
    run_spent_cents: 120,
    run_budget_cents: 100,
  },
});
if (!(bErr instanceof BudgetExceededError)) throw new Error("Expected BudgetExceededError");
if (bErr.spentUsd !== 1.20) throw new Error(`Expected spentUsd 1.20, got ${bErr.spentUsd}`);
if (bErr.budgetUsd !== 1.00) throw new Error(`Expected budgetUsd 1.00, got ${bErr.budgetUsd}`);
console.log("✓ BudgetExceededError correctly parsed ($1.20 spent vs $1.00 budget)");

const rErr = parseSpendGuardError(429, {
  error: {
    message: "Runaway loop detected: consecutive identical tool calls",
    scope: "runaway_loop",
    reason: "repeated_tool_calls",
    run_id: "run-test-2",
    step_count: 14,
  },
});
if (!(rErr instanceof RunawayLoopError)) throw new Error("Expected RunawayLoopError");
if (rErr.stepCount !== 14) throw new Error(`Expected stepCount 14, got ${rErr.stepCount}`);
console.log("✓ RunawayLoopError correctly parsed (step 14)");

const aErr = parseSpendGuardError(401, { error: { message: "Invalid API key" } });
if (!(aErr instanceof AuthenticationError)) throw new Error("Expected AuthenticationError");
console.log("✓ AuthenticationError correctly parsed");

// 2. Fetch Proxy Test (createSpendGuardFetch)
let capturedUrl = "";
let capturedHeaders = null;

const mockFetch = async (input, init) => {
  capturedUrl = typeof input === "string" ? input : input.url;
  capturedHeaders = init?.headers;
  const headers = new Headers({
    "Content-Type": "application/json",
    "X-SpendGuard-Request-Cost-Cents": "5",
    "X-SpendGuard-Run-Spent-Cents": "45",
    "X-SpendGuard-Run-Remaining-Cents": "105",
    "X-SpendGuard-Run-Step": "3",
    "X-SpendGuard-Run-Id": "agent-fetch-run",
  });
  return new Response(JSON.stringify({ status: "ok" }), {
    status: 200,
    headers,
  });
};

let telemetryReceived = null;
const spendguardFetchFn = createSpendGuardFetch({
  gatewayUrl: "http://localhost:8000",
  apiKey: "sg-live-testkey",
  runId: "agent-fetch-run",
  agentId: "agent-007",
  budget: 1.50, // $1.50 -> 150 cents
  maxSteps: 20,
  fetch: mockFetch,
  onTelemetry: (t) => {
    telemetryReceived = t;
  },
});

// Call upstream OpenAI URL
const resp = await spendguardFetchFn("https://api.openai.com/v1/chat/completions", {
  method: "POST",
  body: JSON.stringify({ model: "gpt-4o", messages: [] }),
});

if (capturedUrl !== "http://localhost:8000/v1/chat/completions") {
  throw new Error(`URL rewrite failed: expected http://localhost:8000/v1/chat/completions, got ${capturedUrl}`);
}
console.log(`✓ Fetch proxy URL rewritten: ${capturedUrl}`);

if (capturedHeaders.get("Authorization") !== "Bearer sg-live-testkey") {
  throw new Error("Authorization header missing or incorrect");
}
if (capturedHeaders.get("X-SpendGuard-Run-Id") !== "agent-fetch-run") {
  throw new Error("X-SpendGuard-Run-Id missing");
}
if (capturedHeaders.get("X-SpendGuard-Run-Budget-Cents") !== "150") {
  throw new Error(`Expected 150 budget cents, got ${capturedHeaders.get("X-SpendGuard-Run-Budget-Cents")}`);
}
if (capturedHeaders.get("X-SpendGuard-Max-Steps") !== "20") {
  throw new Error("X-SpendGuard-Max-Steps missing");
}
console.log("✓ Injected SpendGuard headers verified");

if (!resp.spendguard || resp.spendguard.costUsd !== 0.05) {
  throw new Error(`Telemetry cost extraction failed, got ${resp.spendguard?.costUsd}`);
}
if (telemetryReceived?.runSpentUsd !== 0.45) {
  throw new Error(`onTelemetry callback failed, got ${telemetryReceived?.runSpentUsd}`);
}
console.log("✓ Telemetry extracted ($0.05 request cost, $0.45 run spent)");

// 3. OpenAI Client Wrapper Test (wrapOpenAI)
class MockOpenAI {
  constructor(opts) {
    this.baseURL = opts?.baseURL || "https://api.openai.com/v1";
    this.apiKey = opts?.apiKey || "sk-dummy";
    this.chat = {
      completions: {
        create: async (body, reqOpts) => {
          if (body.simulateBudgetExceeded) {
            const err = new Error("Budget exceeded");
            err.status = 429;
            err.error = {
              message: "Hard limit exceeded for run",
              scope: "run_budget",
              reason: "run_budget_exhausted",
              run_spent_cents: 105,
              run_budget_cents: 100,
            };
            throw err;
          }
          return {
            id: "chatcmpl-mock",
            choices: [{ message: { content: "SpendGuard protected response" } }],
            headers: {
              "x-spendguard-request-cost-cents": "10",
              "x-spendguard-run-spent-cents": "75",
            },
          };
        },
      },
    };
  }
}

const mockClient = new MockOpenAI({ apiKey: "sk-openai" });
const wrapped = wrapOpenAI(mockClient, {
  gatewayUrl: "http://localhost:8000",
  apiKey: "sg-live-wrapped",
  runId: "run-wrapped-1",
  budget: 1.00,
  maxSteps: 15,
});

if (wrapped.baseURL !== "http://localhost:8000/v1") {
  throw new Error(`OpenAI baseURL not rewritten, got ${wrapped.baseURL}`);
}
console.log(`✓ wrapOpenAI baseURL updated: ${wrapped.baseURL}`);

const comp = await wrapped.chat.completions.create({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Hello" }],
});
if (comp.choices[0].message.content !== "SpendGuard protected response") {
  throw new Error("Unexpected completion content");
}
if (comp._spendguard?.costUsd !== 0.10) {
  throw new Error(`Expected costUsd 0.10, got ${comp._spendguard?.costUsd}`);
}
console.log("✓ wrapOpenAI completion handled and enriched with telemetry");

// Test budget limit error interception in wrapped client
try {
  await wrapped.chat.completions.create({
    model: "gpt-4o",
    messages: [{ role: "user", content: "Loop" }],
    simulateBudgetExceeded: true,
  });
  throw new Error("Should have thrown BudgetExceededError");
} catch (err) {
  if (!(err instanceof BudgetExceededError)) {
    throw new Error(`Expected BudgetExceededError, got: ${err.constructor.name}: ${err.message}`);
  }
  console.log(`✓ wrapOpenAI caught 429 and re-threw typed BudgetExceededError: ${err.message}`);
}

// 4. SpendGuard Main Client
const sg = new SpendGuard({
  apiKey: "sg-test-main",
  baseUrl: "http://localhost:8000",
  defaultBudget: 2.50,
});
if (sg.baseUrl !== "http://localhost:8000") throw new Error("BaseURL mismatch");
console.log("✓ SpendGuard client instance initialized");

console.log("\n=== ALL sg-run TESTS PASSED! ===");
