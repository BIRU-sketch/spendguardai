/**
 * SpendGuard — Agent Run Trajectory Context.
 *
 * Tracks cumulative cost, step iteration, remaining budget,
 * and lifecycle events for an autonomous agent execution.
 */
import { AgentRunOptions, CustomFetch, RunStatus } from "./types.js";
export interface RunManagerConfig {
    baseUrl: string;
    apiKey: string;
    timeout?: number;
    fetch?: CustomFetch;
}
export declare class SpendGuardRun {
    private readonly config;
    readonly runId: string;
    readonly agentId?: string;
    readonly sessionId?: string;
    budgetUsd?: number;
    maxSteps?: number;
    metadata: Record<string, any>;
    autoComplete: boolean;
    spentUsd: number;
    remainingUsd?: number;
    step: number;
    status: string;
    isAborted: boolean;
    constructor(options: AgentRunOptions, config: RunManagerConfig);
    private request;
    /**
     * Registers the run trajectory with the SpendGuard gateway.
     */
    init(): Promise<this>;
    /**
     * Refreshes run spend and step metrics from the gateway.
     */
    refresh(): Promise<RunStatus>;
    /**
     * Dynamically assigns or updates the spend ceiling in USD.
     */
    setBudget(budgetUsd: number): Promise<RunStatus>;
    /**
     * Marks this trajectory completed.
     */
    complete(): Promise<RunStatus>;
    /**
     * Emergency circuit breaker: immediately halts this run.
     */
    abort(reason?: string): Promise<RunStatus>;
    /**
     * Resets runaway circuit breakers and step counters for this run.
     */
    reset(): Promise<RunStatus>;
    /**
     * Creates a custom fetch bound to this specific agent run trajectory.
     */
    createFetch(extraOptions?: {
        rewriteHost?: boolean;
    }): CustomFetch;
    /**
     * Wraps an OpenAI client instance bound to this agent trajectory.
     */
    wrapOpenAI<T extends {
        baseURL?: string;
        chat?: any;
        [key: string]: any;
    }>(client: T): T;
    /**
     * Normalizes raw run payload to strongly typed RunStatus.
     */
    private applyStatus;
}
//# sourceMappingURL=run.d.ts.map