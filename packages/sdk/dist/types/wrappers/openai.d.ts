/**
 * SpendGuard — Native OpenAI SDK Wrapper.
 *
 * Wraps official `OpenAI` client instances to enforce agent run boundaries,
 * pre-flight budget reservations, runaway loop detection, and cost telemetry.
 */
import { AgentRunOptions, SpendGuardTelemetry } from "../types.js";
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
export declare function wrapOpenAI<T extends {
    baseURL?: string;
    chat?: any;
    [key: string]: any;
}>(client: T, options?: WrapOpenAIOptions): T & {
    spendguard: SpendGuardControl;
};
//# sourceMappingURL=openai.d.ts.map