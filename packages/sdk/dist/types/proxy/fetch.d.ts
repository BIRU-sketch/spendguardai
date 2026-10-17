/**
 * SpendGuard Fetch-Based Proxy.
 *
 * Provides a drop-in replacement for standard `fetch` compatible with:
 * - Vercel AI SDK (`@ai-sdk/openai`, `@ai-sdk/anthropic`)
 * - LangChain.js / LangGraph.js
 * - Mastra, LlamaIndexTS, and custom agent loops.
 */
import { CustomFetch, SpendGuardFetchOptions, SpendGuardTelemetry } from "../types.js";
/**
 * Extracts SpendGuard telemetry headers from an HTTP Response.
 */
export declare function extractTelemetryFromHeaders(headers: Headers): SpendGuardTelemetry;
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
export declare function createSpendGuardFetch(options?: SpendGuardFetchOptions): CustomFetch;
/**
 * Direct fetch execution through SpendGuard gateway.
 */
export declare function spendguardFetch(input: RequestInfo | URL, init?: RequestInit, options?: SpendGuardFetchOptions): Promise<Response>;
//# sourceMappingURL=fetch.d.ts.map