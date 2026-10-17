/**
 * SpendGuard Fetch-Based Proxy.
 *
 * Provides a drop-in replacement for standard `fetch` compatible with:
 * - Vercel AI SDK (`@ai-sdk/openai`, `@ai-sdk/anthropic`)
 * - LangChain.js / LangGraph.js
 * - Mastra, LlamaIndexTS, and custom agent loops.
 */
import { SpendGuardHeaders, } from "../types.js";
/**
 * Extracts SpendGuard telemetry headers from an HTTP Response.
 */
export function extractTelemetryFromHeaders(headers) {
    const getHeader = (name) => headers.get(name);
    const costCents = parseInt(getHeader(SpendGuardHeaders.REQUEST_COST_CENTS) || "0", 10);
    const runSpentCents = getHeader(SpendGuardHeaders.RUN_SPENT_CENTS)
        ? parseInt(getHeader(SpendGuardHeaders.RUN_SPENT_CENTS), 10)
        : undefined;
    const runRemainingCents = getHeader(SpendGuardHeaders.RUN_REMAINING_CENTS)
        ? parseInt(getHeader(SpendGuardHeaders.RUN_REMAINING_CENTS), 10)
        : undefined;
    const projRemainingCents = getHeader(SpendGuardHeaders.PROJECT_REMAINING_CENTS)
        ? parseInt(getHeader(SpendGuardHeaders.PROJECT_REMAINING_CENTS), 10)
        : undefined;
    const step = getHeader(SpendGuardHeaders.RUN_STEP)
        ? parseInt(getHeader(SpendGuardHeaders.RUN_STEP), 10)
        : undefined;
    return {
        costCents,
        costUsd: costCents / 100,
        runId: getHeader(SpendGuardHeaders.RUN_ID) || undefined,
        agentId: getHeader(SpendGuardHeaders.AGENT_ID) || undefined,
        step,
        runSpentCents,
        runSpentUsd: runSpentCents !== undefined ? runSpentCents / 100 : undefined,
        runRemainingCents,
        runRemainingUsd: runRemainingCents !== undefined ? runRemainingCents / 100 : undefined,
        projectRemainingCents: projRemainingCents,
        projectRemainingUsd: projRemainingCents !== undefined ? projRemainingCents / 100 : undefined,
    };
}
/**
 * Creates a fetch function that routes requests through SpendGuard,
 * automatically injecting agent identity, run ceilings, and extracting cost telemetry.
 *
 * @example
 * ```ts
 * import { createOpenAI } from '@ai-sdk/openai';
 * import { createSpendGuardFetch } from 'sg-run';
 *
 * const openai = createOpenAI({
 *   apiKey: process.env.SPENDGUARD_API_KEY,
 *   baseURL: 'http://localhost:8000/v1',
 *   fetch: createSpendGuardFetch({
 *     runId: 'agent-run-123',
 *     budget: 1.00, // $1.00 max spend
 *     maxSteps: 30, // 30 max iterations
 *   }),
 * });
 * ```
 */
export function createSpendGuardFetch(options = {}) {
    const baseFetch = options.fetch || (globalThis.fetch ? globalThis.fetch.bind(globalThis) : undefined);
    if (!baseFetch) {
        throw new Error("No fetch implementation found. Ensure globalThis.fetch is available or provide custom fetch option.");
    }
    const rawGateway = (options.gatewayUrl ||
        (typeof process !== "undefined" ? process.env?.SPENDGUARD_BASE_URL || process.env?.SPENDGUARD_GATEWAY_URL : undefined) ||
        "http://localhost:8000").replace(/\/+$/, "");
    const defaultApiKey = options.apiKey ||
        (typeof process !== "undefined"
            ? process.env?.SPENDGUARD_API_KEY || process.env?.ADMIN_API_KEY || process.env?.GATEWAY_API_KEY
            : undefined) ||
        "";
    const budgetCents = options.budgetCents !== undefined
        ? options.budgetCents
        : options.budget !== undefined
            ? Math.round(options.budget * 100)
            : undefined;
    const rewriteHost = options.rewriteHost !== false;
    return async (input, init) => {
        let targetUrl;
        if (typeof input === "string") {
            targetUrl = input;
        }
        else if (input instanceof URL) {
            targetUrl = input.toString();
        }
        else if (input && typeof input.url === "string") {
            targetUrl = input.url;
        }
        else {
            targetUrl = String(input);
        }
        // Rewrite LLM upstream endpoints to SpendGuard gateway if configured
        if (rewriteHost) {
            try {
                const parsed = new URL(targetUrl, rawGateway);
                const host = parsed.hostname.toLowerCase();
                // If target points to known upstream providers, redirect to SpendGuard gateway
                if (host.includes("openai.com") ||
                    host.includes("anthropic.com") ||
                    host.includes("googleapis.com") ||
                    host.includes("groq.com") ||
                    host.includes("openrouter.ai")) {
                    const pathWithQuery = parsed.pathname + parsed.search;
                    targetUrl = `${rawGateway}${pathWithQuery}`;
                }
                else if (targetUrl.startsWith("/")) {
                    targetUrl = `${rawGateway}${targetUrl}`;
                }
            }
            catch {
                // Fallback to original targetUrl
            }
        }
        // Build header map
        const headers = new Headers(init?.headers || (typeof input === "object" && "headers" in input ? input.headers : undefined));
        // Inject authentication
        if (defaultApiKey && (!headers.has("Authorization") || !headers.get("Authorization"))) {
            headers.set("Authorization", `Bearer ${defaultApiKey}`);
        }
        // Inject SpendGuard agent run boundaries
        if (options.runId && !headers.has(SpendGuardHeaders.RUN_ID)) {
            headers.set(SpendGuardHeaders.RUN_ID, options.runId);
        }
        if (options.agentId && !headers.has(SpendGuardHeaders.AGENT_ID)) {
            headers.set(SpendGuardHeaders.AGENT_ID, options.agentId);
        }
        if (options.sessionId && !headers.has(SpendGuardHeaders.SESSION_ID)) {
            headers.set(SpendGuardHeaders.SESSION_ID, options.sessionId);
        }
        if (budgetCents !== undefined && !headers.has(SpendGuardHeaders.RUN_BUDGET_CENTS)) {
            headers.set(SpendGuardHeaders.RUN_BUDGET_CENTS, String(budgetCents));
        }
        if (options.maxSteps !== undefined && !headers.has(SpendGuardHeaders.MAX_STEPS)) {
            headers.set(SpendGuardHeaders.MAX_STEPS, String(options.maxSteps));
        }
        if (options.provider && !headers.has(SpendGuardHeaders.PROVIDER)) {
            headers.set(SpendGuardHeaders.PROVIDER, options.provider);
        }
        const modifiedInit = {
            ...init,
            headers,
        };
        const response = await baseFetch(targetUrl, modifiedInit);
        // Extract SpendGuard telemetry from headers
        const telemetry = extractTelemetryFromHeaders(response.headers);
        response.spendguard = telemetry;
        if (options.onTelemetry && (telemetry.costCents > 0 || telemetry.runId)) {
            try {
                options.onTelemetry(telemetry);
            }
            catch (err) {
                console.error("SpendGuard: Error in onTelemetry callback:", err);
            }
        }
        return response;
    };
}
/**
 * Direct fetch execution through SpendGuard gateway.
 */
export async function spendguardFetch(input, init, options) {
    const fetchFn = createSpendGuardFetch(options);
    return fetchFn(input, init);
}
//# sourceMappingURL=fetch.js.map