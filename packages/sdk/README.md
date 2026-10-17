# sg-run

> **Agent-Native Hard Cost Control Layer SDK for TypeScript, Node.js, and Python**  
> Prevent runaway agent spend, detect recursive tool loops, and lock spend ceilings before tokens are generated.

---

## Features

- **Standard `wrapOpenAI(client, options)`**: Drop-in wrapper around official `openai` Node SDK instances. Rewrites `baseURL`, injects run boundaries, and maps HTTP 429 overruns into strongly typed exceptions.
- **Fetch-Based Proxy (`createSpendGuardFetch`)**: Universal drop-in `fetch` function compatible with **Vercel AI SDK**, **LangChain.js**, **Mastra**, **LlamaIndexTS**, and native `fetch`.
- **Pre-Flight Atomic Spend Ceilings**: Enforce hard dollar budgets per agent run trajectory (`budget: 1.50`).
- **Runaway Loop Circuit Breakers**: Automatically halts runs that exceed trajectory step caps, burst-loop, or repeatedly execute identical tool calls.
- **Typed Error Hierarchy**: Handles `BudgetExceededError`, `RunawayLoopError`, and `RunAbortedError`.
- **Real-Time Telemetry**: Real-time extraction of request cost, cumulative run spend, and remaining budget from gateway headers.

---

## Installation

```bash
npm install sg-run
# Optional: if using with OpenAI SDK
npm install openai
```

---

## Quickstart

### 1. Wrapping Official `openai` SDK with `wrapOpenAI`

```typescript
import OpenAI from "openai";
import { wrapOpenAI, BudgetExceededError, RunawayLoopError } from "sg-run";

// 1. Wrap your existing OpenAI client
const openai = wrapOpenAI(new OpenAI({ apiKey: "sk-openai-key" }), {
  gatewayUrl: "http://localhost:8000",
  apiKey: "sg-live-your-spendguard-key",
  runId: "research-agent-01",
  agentId: "web-researcher",
  budget: 1.50, // Hard ceiling: $1.50 USD
  maxSteps: 25, // Runaway tripwire: max 25 iterations
});

async function main() {
  try {
    const completion = await openai.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: "Analyze Q3 revenue report" }],
    });

    console.log("Agent response:", completion.choices[0].message.content);

    // SpendGuard cost telemetry is automatically attached
    console.log("Request cost:", `$${completion._spendguard?.costUsd}`);
    console.log("Run cumulative spent:", `$${completion._spendguard?.runSpentUsd}`);
  } catch (error) {
    if (error instanceof BudgetExceededError) {
      console.error(`Execution blocked! Spent $${error.spentUsd} of $${error.budgetUsd} budget.`);
    } else if (error instanceof RunawayLoopError) {
      console.error(`Runaway loop detected at step ${error.stepCount}! Reason: ${error.reason}`);
    } else {
      throw error;
    }
  }
}

main();
```

---

### 2. Fetch-Based Proxy with Vercel AI SDK (`@ai-sdk/openai`)

Use `createSpendGuardFetch` as a custom `fetch` implementation for the Vercel AI SDK:

```typescript
import { createOpenAI } from "@ai-sdk/openai";
import { generateText } from "ai";
import { createSpendGuardFetch } from "sg-run";

// Create custom SpendGuard fetch
const spendguardFetch = createSpendGuardFetch({
  gatewayUrl: "http://localhost:8000",
  apiKey: process.env.SPENDGUARD_API_KEY,
  runId: "support-agent-run-88",
  agentId: "customer-support-v2",
  budget: 0.75, // $0.75 hard limit
  maxSteps: 15,
  onTelemetry: (t) => {
    console.log(`[SpendGuard] Cost: $${t.costUsd} | Run Spent: $${t.runSpentUsd}`);
  },
});

const openai = createOpenAI({
  apiKey: process.env.OPENAI_API_KEY,
  baseURL: "http://localhost:8000/v1",
  fetch: spendguardFetch,
});

async function run() {
  const { text } = await generateText({
    model: openai("gpt-4o-mini"),
    prompt: "Summarize recent customer support tickets.",
  });
  console.log(text);
}
```

---

### 3. Fetch-Based Proxy with LangChain.js

Pass `createSpendGuardFetch` into any LangChain model:

```typescript
import { ChatOpenAI } from "@langchain/openai";
import { createSpendGuardFetch } from "sg-run";

const model = new ChatOpenAI({
  model: "gpt-4o",
  configuration: {
    baseURL: "http://localhost:8000/v1",
    fetch: createSpendGuardFetch({
      apiKey: process.env.SPENDGUARD_API_KEY,
      runId: "langchain-agent-42",
      budget: 2.00,
      maxSteps: 30,
    }),
  },
});

const response = await model.invoke("Draft an executive summary.");
console.log(response.content);
```

---

### 4. Native SpendGuard Client & Trajectory Management

```typescript
import { SpendGuard } from "sg-run";

const client = new SpendGuard({
  apiKey: process.env.SPENDGUARD_API_KEY,
  baseUrl: "http://localhost:8000",
});

async function runAgentTrajectory() {
  // 1. Initialize an agent trajectory
  const run = await client.run({
    agentId: "financial-analyst",
    budget: 3.00, // $3.00 USD
    maxSteps: 50,
  });

  console.log(`Initialized run: ${run.runId}`);

  // 2. Execute bounded LLM steps
  const res = await client.chat({
    model: "claude-3-5-sonnet", // routed dynamically to Anthropic
    messages: [{ role: "user", content: "Calculate ARR growth" }],
    runId: run.runId,
  });

  console.log("Output:", res.content);
  console.log("Spent so far:", `$${run.spentUsd}`);
  console.log("Remaining budget:", `$${run.remainingUsd}`);

  // 3. Dynamically extend budget or emergency kill
  // await run.setBudget(5.00); // Increase budget to $5.00
  // await run.abort("user_requested_cancel"); // Kill-switch

  // 4. Mark run complete
  await run.complete();
}
```

---

## Error Handling

SpendGuard returns **HTTP 429** when limits are exceeded. The SDK parses these into typed exceptions:

```typescript
import {
  BudgetExceededError,
  RunawayLoopError,
  RunAbortedError,
  AuthenticationError,
} from "sg-run";

try {
  // LLM call
} catch (err) {
  if (err instanceof BudgetExceededError) {
    console.error("Budget Ceiling Tripped:", {
      scope: err.scope, // "run_budget" | "project_balance"
      spentUsd: err.spentUsd,
      budgetUsd: err.budgetUsd,
      runId: err.runId,
    });
  } else if (err instanceof RunawayLoopError) {
    console.error("Runaway Loop Detected:", {
      reason: err.reason, // "max_steps_exceeded" | "repeated_tool_calls" | "rapid_burst"
      stepCount: err.stepCount,
      runId: err.runId,
    });
  } else if (err instanceof RunAbortedError) {
    console.error("Run Halted by Kill-Switch:", err.reason);
  }
}
```

---

## Telemetry Headers

Every request processed by SpendGuard gateway injects real-time cost accounting:

| Header | Description |
| :--- | :--- |
| `X-SpendGuard-Request-Cost-Cents` | Cost of the current request in integer cents. |
| `X-SpendGuard-Run-Spent-Cents` | Cumulative spend for this agent trajectory. |
| `X-SpendGuard-Run-Remaining-Cents` | Remaining budget ceiling for this run. |
| `X-SpendGuard-Run-Step` | Current trajectory step iteration. |
| `X-SpendGuard-Project-Remaining-Cents`| Remaining workspace wallet balance. |

---

## License

Apache-2.0
