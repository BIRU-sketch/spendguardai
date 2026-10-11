import { readFile } from "node:fs/promises";

async function loadDotEnv() {
  try {
    const contents = await readFile(new URL("./.env", import.meta.url), "utf8");
    for (const line of contents.split(/\r?\n/)) {
      const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
      if (!match || match[1] in process.env) continue;
      let value = match[2];
      if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
      ) {
        value = value.slice(1, -1);
      } else {
        value = value.replace(/\s+#.*$/, "");
      }
      process.env[match[1]] = value;
    }
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

await loadDotEnv();

const results = [];
const baseUrl = (process.env.SPENDGUARD_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
const apiKey = process.env.GATEWAY_API_KEY || "sk-live-7dkSfncQmls35X_EUk_4r9nTVtuplOVuxDEE65ng2ks";
const model = process.env.GATEWAY_MODEL || "inclusionai/ling-3.0-flash-sante:free";
const skipChat = process.argv.includes("--skip-chat");

async function check(name, request, expectedStatus, validate = () => {}) {
  try {
    const response = await request();
    const text = await response.text();
    let body;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = text;
    }
    if (response.status !== expectedStatus) {
      throw new Error(`expected HTTP ${expectedStatus}, got ${response.status}: ${text}`);
    }
    validate(body);
    results.push({ name, ok: true, status: response.status });
    console.log(`PASS ${name} (HTTP ${response.status})`);
    return body;
  } catch (error) {
    results.push({ name, ok: false, error: error.message });
    console.error(`FAIL ${name}: ${error.message}`);
    return null;
  }
}

function get(path, headers = {}, authenticated = true) {
  return fetch(`${baseUrl}${path}`, {
    headers: {
      ...(authenticated ? { Authorization: `Bearer ${apiKey}` } : {}),
      ...headers,
    },
  });
}

function post(path, body, headers = {}, authenticated = true) {
  return fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(authenticated ? { Authorization: `Bearer ${apiKey}` } : {}),
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

async function main() {
  console.log(`SpendGuard Core Gateway Server test: ${baseUrl}`);
  await check(
    "health endpoint",
    () => get("/healthz", {}, false),
    200,
    (body) => {
      if (body?.status !== "ok") throw new Error("health response was not {status: 'ok'}");
    },
  );

  await check(
    "unauthenticated access rejected",
    () => post("/chat/completions", { model: "gpt-4o", messages: [] }, {}, false),
    401,
  );

  const testRunId = `mjs-run-${Date.now()}`;

  // SDK Run Registration
  await check(
    "register agent run",
    () =>
      post("/v1/runs", {
        run_id: testRunId,
        agent_id: "node-agent-test",
        budget_cents: 100,
        max_steps: 25,
      }),
    200,
    (body) => {
      if (body?.run?.run_id !== testRunId) throw new Error("run registration failed");
    },
  );

  if (skipChat) {
    console.log("SKIP chat completion (--skip-chat)");
  } else {
    await check(
      "chat completion with agent identity and budget",
      () =>
        post(
          "/v1/chat/completions",
          {
            model,
            messages: [{ role: "user", content: "Reply with exactly: SpendGuard gateway OK" }],
            max_tokens: 512,
          },
          {
            "X-SpendGuard-Run-Id": testRunId,
            "X-SpendGuard-Agent-Id": "node-agent-test",
          },
        ),
      200,
      (body) => {
        const choice = body?.choices?.[0];
        const textContent = choice?.message?.content || choice?.message?.reasoning;
        if (!textContent) {
          throw new Error("response has no assistant text content");
        }
        console.log(`  Model response: ${textContent.slice(0, 80)}...`);
      },
    );

    await check(
      "agent run status query",
      () => get(`/runs/${testRunId}`),
      200,
      (body) => {
        if (body?.run?.run_id !== testRunId) {
          throw new Error("run status returned unexpected run_id");
        }
      },
    );

    await check(
      "abort agent run",
      () => post(`/runs/${testRunId}/abort`, { reason: "mjs_test_abort" }),
      200,
      (body) => {
        if (!body?.aborted) throw new Error("run was not aborted");
      },
    );

    await check(
      "gateway blocks aborted run",
      () =>
        post(
          "/v1/chat/completions",
          {
            model,
            messages: [{ role: "user", content: "Should be blocked" }],
          },
          {
            "X-SpendGuard-Run-Id": testRunId,
          },
        ),
      429,
    );
  }

  const failed = results.filter((result) => !result.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed.`);
  if (failed.length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(`Smoke test could not run: ${error.message}`);
  process.exitCode = 1;
});
