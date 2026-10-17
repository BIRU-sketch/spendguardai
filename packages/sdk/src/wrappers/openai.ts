/**
 * SpendGuard — Native OpenAI SDK Wrapper.
 *
 * Wraps official `OpenAI` client instances to enforce agent run boundaries,
 * pre-flight budget reservations, runaway loop detection, and cost telemetry.
 */

import { parseSpendGuardError, SpendGuardError } from "../errors.js";
import { AgentRunOptions, SpendGuardHeaders, SpendGuardTelemetry } from "../types.js";

export interface WrapOpenAIOptions extends AgentRunOptions {
  /** Gateway base URL (defaults to SPENDGUARD_BASE_URL or http://localhost:8000) */
  gatewayUrl?: string;
  /** SpendGuard API key (defaults to SPENDGUARD_API_KEY, ADMIN_API_KEY, or GATEWAY_API_KEY) */
  apiKey?: string;
}

export interface SpendGuardControl {
  setRun(options: AgentRunOptions): void;
  getTelemetry(): SpendGuardTelemetry | undefined;
}

/**
 * Wraps an OpenAI client instance with SpendGuard hard cost control.
 *
 * @example
 * ```ts
 * import OpenAI from 'openai';
 * import { wrapOpenAI } from 'sg-run';
 *
 * const openai = wrapOpenAI(new OpenAI({ apiKey: 'sk-...' }), {
 *   gatewayUrl: 'http://localhost:8000',
 *   apiKey: 'sg-live-...',
 *   runId: 'agent-run-123',
 *   budget: 1.50, // $1.50 ceiling
 *   maxSteps: 25, // 25 step cap
 * });
 *
 * try {
 *   const completion = await openai.chat.completions.create({
 *     model: 'gpt-4o',
 *     messages: [{ role: 'user', content: 'Analyze Q3 report' }],
 *   });
 *   console.log('Cost:', completion._spendguard?.costUsd);
 * } catch (err) {
 *   if (err instanceof BudgetExceededError) {
 *     console.error('Run budget exceeded:', err.spentUsd);
 *   }
 * }
 * ```
 */
export function wrapOpenAI<T extends { baseURL?: string; chat?: any; [key: string]: any }>(
  client: T,
  options: WrapOpenAIOptions = {},
): T & { spendguard: SpendGuardControl } {
  const gatewayUrl = (
    options.gatewayUrl ||
    (typeof process !== "undefined" ? process.env?.SPENDGUARD_BASE_URL || process.env?.SPENDGUARD_GATEWAY_URL : undefined) ||
    "http://localhost:8000"
  ).replace(/\/+$/, "");

  const fullGatewayV1 = gatewayUrl.endsWith("/v1") ? gatewayUrl : `${gatewayUrl}/v1`;

  // Redirect client baseURL to SpendGuard gateway /v1
  if ("baseURL" in client) {
    client.baseURL = fullGatewayV1;
  }

  const spendguardApiKey =
    options.apiKey ||
    (typeof process !== "undefined"
      ? process.env?.SPENDGUARD_API_KEY || process.env?.ADMIN_API_KEY || process.env?.GATEWAY_API_KEY
      : undefined);

  // Active run configuration
  let currentRunOptions: AgentRunOptions = {
    runId: options.runId,
    agentId: options.agentId,
    sessionId: options.sessionId,
    budget: options.budget,
    budgetCents: options.budgetCents,
    maxSteps: options.maxSteps,
    provider: options.provider,
  };

  let lastTelemetry: SpendGuardTelemetry | undefined = undefined;

  const originalCreate = client.chat?.completions?.create?.bind(client.chat.completions);

  if (typeof originalCreate !== "function") {
    throw new Error("Unable to wrap client: client.chat.completions.create is not a function");
  }

  // Intercept client.chat.completions.create
  client.chat.completions.create = async function wrappedCreate(
    body: any,
    requestOptions: any = {},
  ): Promise<any> {
    const extraHeaders: Record<string, string> = {
      ...(requestOptions?.headers || {}),
    };

    if (spendguardApiKey && !extraHeaders["Authorization"]) {
      extraHeaders["Authorization"] = `Bearer ${spendguardApiKey}`;
    }

    const runId = requestOptions?.spendguard?.runId || currentRunOptions.runId;
    const agentId = requestOptions?.spendguard?.agentId || currentRunOptions.agentId;
    const sessionId = requestOptions?.spendguard?.sessionId || currentRunOptions.sessionId;
    const provider = requestOptions?.spendguard?.provider || currentRunOptions.provider;

    const budgetCents =
      requestOptions?.spendguard?.budgetCents ??
      currentRunOptions.budgetCents ??
      (requestOptions?.spendguard?.budget !== undefined
        ? Math.round(requestOptions.spendguard.budget * 100)
        : currentRunOptions.budget !== undefined
        ? Math.round(currentRunOptions.budget * 100)
        : undefined);

    const maxSteps = requestOptions?.spendguard?.maxSteps ?? currentRunOptions.maxSteps;

    if (runId) extraHeaders[SpendGuardHeaders.RUN_ID] = runId;
    if (agentId) extraHeaders[SpendGuardHeaders.AGENT_ID] = agentId;
    if (sessionId) extraHeaders[SpendGuardHeaders.SESSION_ID] = sessionId;
    if (budgetCents !== undefined) extraHeaders[SpendGuardHeaders.RUN_BUDGET_CENTS] = String(budgetCents);
    if (maxSteps !== undefined) extraHeaders[SpendGuardHeaders.MAX_STEPS] = String(maxSteps);
    if (provider) extraHeaders[SpendGuardHeaders.PROVIDER] = provider;

    const mergedRequestOptions = {
      ...requestOptions,
      headers: extraHeaders,
    };

    try {
      // In modern OpenAI Node SDK, options can be passed with headers
      const result = await originalCreate(body, mergedRequestOptions);

      // If streaming response, wrap the async generator
      if (body.stream && result && typeof (result as any)[Symbol.asyncIterator] === "function") {
        return (async function* () {
          for await (const chunk of result) {
            yield chunk;
          }
        })();
      }

      // If response includes response headers or raw object
      if (result && typeof result === "object") {
        // Attach SpendGuard telemetry if present on the underlying response
        const headers = (result as any)._response?.headers || (result as any).headers;
        if (headers) {
          const costCents = parseInt(headers.get ? headers.get(SpendGuardHeaders.REQUEST_COST_CENTS) : headers[SpendGuardHeaders.REQUEST_COST_CENTS.toLowerCase()] || "0", 10);
          const runSpentCents = parseInt(headers.get ? headers.get(SpendGuardHeaders.RUN_SPENT_CENTS) : headers[SpendGuardHeaders.RUN_SPENT_CENTS.toLowerCase()] || "0", 10);
          lastTelemetry = {
            costCents,
            costUsd: costCents / 100,
            runId,
            agentId,
            runSpentCents,
            runSpentUsd: runSpentCents / 100,
          };
          (result as any)._spendguard = lastTelemetry;
        }
      }

      return result;
    } catch (err: any) {
      // Parse OpenAI API errors into typed SpendGuard errors
      const status = err?.status || err?.statusCode || (err instanceof SpendGuardError ? err.statusCode : undefined);
      const errorBody = err?.error || err?.body || err;

      if (status === 429 || status === 401 || status === 502) {
        throw parseSpendGuardError(status, errorBody, err.message);
      }

      throw err;
    }
  };

  const spendguardControl: SpendGuardControl = {
    setRun(newOptions: AgentRunOptions) {
      currentRunOptions = { ...currentRunOptions, ...newOptions };
    },
    getTelemetry() {
      return lastTelemetry;
    },
  };

  (client as any).spendguard = spendguardControl;

  return client as T & { spendguard: SpendGuardControl };
}
