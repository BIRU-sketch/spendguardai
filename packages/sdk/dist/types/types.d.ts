/**
 * SpendGuard SDK — Data Models and Types.
 */
export interface TokenUsage {
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
    cachedTokens?: number;
    reasoningTokens?: number;
}
export interface SpendGuardTelemetry {
    /** Cost of this specific request in USD cents */
    costCents: number;
    /** Cost of this specific request in USD */
    costUsd: number;
    /** Agent run trajectory identifier */
    runId?: string;
    /** Agent identifier */
    agentId?: string;
    /** Trajectory step iteration counter */
    step?: number;
    /** Cumulative spend for this run in USD cents */
    runSpentCents?: number;
    /** Cumulative spend for this run in USD */
    runSpentUsd?: number;
    /** Remaining budget ceiling for this run in USD cents */
    runRemainingCents?: number;
    /** Remaining budget ceiling for this run in USD */
    runRemainingUsd?: number;
    /** Workspace project remaining balance in USD cents */
    projectRemainingCents?: number;
    /** Workspace project remaining balance in USD */
    projectRemainingUsd?: number;
}
export interface ChatMessage {
    role: "system" | "user" | "assistant" | "tool" | "function" | string;
    content: string | any;
    name?: string;
    tool_call_id?: string;
    tool_calls?: any[];
}
export interface ChatCompletionOptions {
    model: string;
    messages: ChatMessage[] | string;
    runId?: string;
    agentId?: string;
    sessionId?: string;
    /** Hard spend ceiling for this run in USD (e.g. 0.50) */
    budget?: number;
    /** Hard spend ceiling for this run in cents (e.g. 50) */
    budgetCents?: number;
    /** Maximum allowed trajectory steps */
    maxSteps?: number;
    /** Target provider override (openai, anthropic, gemini, groq, openrouter) */
    provider?: string;
    maxTokens?: number;
    temperature?: number;
    tools?: any[];
    toolChoice?: any;
    stream?: boolean;
    extraHeaders?: Record<string, string>;
    [key: string]: any;
}
export interface ChatResponse {
    content: string | null;
    model: string;
    finishReason?: string;
    usage: TokenUsage;
    toolCalls?: any[];
    /** Injected SpendGuard real-time cost and budget telemetry */
    spendguard: SpendGuardTelemetry;
    raw: any;
}
export interface RunStatus {
    runId: string;
    projectId: string;
    agentId?: string;
    sessionId?: string;
    budgetCents?: number;
    budgetUsd?: number;
    spentCents: number;
    spentUsd: number;
    remainingCents?: number;
    remainingUsd?: number;
    stepCount: number;
    isAborted: boolean;
    status: "active" | "budget_exceeded" | "runaway_aborted" | "aborted" | "completed" | string;
    metadata: Record<string, any>;
}
export interface SpendGuardClientConfig {
    /** SpendGuard API key (defaults to SPENDGUARD_API_KEY, ADMIN_API_KEY, or GATEWAY_API_KEY env var) */
    apiKey?: string;
    /** SpendGuard Gateway base URL (defaults to SPENDGUARD_BASE_URL or http://localhost:8000) */
    baseUrl?: string;
    /** Request timeout in milliseconds (default: 60000ms) */
    timeout?: number;
    /** Default run budget in USD */
    defaultBudget?: number;
    /** Default runaway step limit */
    defaultMaxSteps?: number;
}
export interface AgentRunOptions {
    runId?: string;
    agentId?: string;
    sessionId?: string;
    /** Hard spend ceiling in USD (e.g. 1.50) */
    budget?: number;
    /** Hard spend ceiling in integer cents (e.g. 150) */
    budgetCents?: number;
    /** Max trajectory steps before circuit breaker trips */
    maxSteps?: number;
    /** Provider hint (optional) */
    provider?: string;
    metadata?: Record<string, any>;
    autoComplete?: boolean;
}
export interface ModelPricing {
    inputCentsPer1k: number;
    outputCentsPer1k: number;
    currency: string;
}
export interface ModelInfo {
    id: string;
    ownedBy: string;
    pricing: ModelPricing;
}
export interface ProjectInfo {
    projectId: string;
    name: string;
    tier: string;
    balanceCents: number;
    balanceUsd: number;
    currency: string;
}
/** Header constants */
export declare const SpendGuardHeaders: {
    readonly RUN_ID: "X-SpendGuard-Run-Id";
    readonly AGENT_ID: "X-SpendGuard-Agent-Id";
    readonly SESSION_ID: "X-SpendGuard-Session-Id";
    readonly RUN_BUDGET_CENTS: "X-SpendGuard-Run-Budget-Cents";
    readonly MAX_STEPS: "X-SpendGuard-Max-Steps";
    readonly PROVIDER: "X-SpendGuard-Provider";
    readonly REQUEST_COST_CENTS: "X-SpendGuard-Request-Cost-Cents";
    readonly RUN_SPENT_CENTS: "X-SpendGuard-Run-Spent-Cents";
    readonly RUN_REMAINING_CENTS: "X-SpendGuard-Run-Remaining-Cents";
    readonly RUN_STEP: "X-SpendGuard-Run-Step";
    readonly PROJECT_REMAINING_CENTS: "X-SpendGuard-Project-Remaining-Cents";
    readonly CURRENCY: "X-SpendGuard-Currency";
};
export type CustomFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
export interface SpendGuardFetchOptions {
    gatewayUrl?: string;
    apiKey?: string;
    runId?: string;
    agentId?: string;
    sessionId?: string;
    budget?: number;
    budgetCents?: number;
    maxSteps?: number;
    provider?: string;
    /** If true, rewrites upstream LLM hostnames (e.g. api.openai.com) to gatewayUrl */
    rewriteHost?: boolean;
    /** Callback fired whenever SpendGuard cost telemetry is received */
    onTelemetry?: (telemetry: SpendGuardTelemetry) => void;
    /** Custom underlying fetch implementation (defaults to globalThis.fetch) */
    fetch?: CustomFetch;
}
//# sourceMappingURL=types.d.ts.map