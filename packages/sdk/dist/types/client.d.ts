/**
 * SpendGuard — Main TypeScript Client.
 *
 * Client for managing agent runs, executing budget-controlled completions,
 * wrapping upstream SDKs, and generating fetch proxies.
 */
import { SpendGuardRun } from "./run.js";
import { AgentRunOptions, ChatCompletionOptions, ChatResponse, CustomFetch, ModelInfo, ProjectInfo, RunStatus, SpendGuardClientConfig } from "./types.js";
import { WrapOpenAIOptions } from "./wrappers/openai.js";
export declare class SpendGuard {
    readonly apiKey: string;
    readonly baseUrl: string;
    readonly timeout: number;
    readonly defaultBudget?: number;
    readonly defaultMaxSteps?: number;
    private readonly _customFetch?;
    readonly chatCompletions: {
        create: (body: any, options?: any) => Promise<any>;
    };
    constructor(config?: SpendGuardClientConfig);
    get completions(): {
        create: (body: any, options?: any) => Promise<any>;
    };
    private request;
    /**
     * Executes a chat completion bound by agent spend limits and runaway protection.
     */
    chat(options: ChatCompletionOptions): Promise<ChatResponse | AsyncIterable<any>>;
    /**
     * Initializes and registers an autonomous agent run trajectory.
     */
    run(options?: AgentRunOptions): Promise<SpendGuardRun>;
    /**
     * Agent run trajectory management endpoints.
     */
    readonly runs: {
        get: (runId: string) => Promise<RunStatus>;
        list: (params?: {
            agentId?: string;
            status?: string;
            limit?: number;
            offset?: number;
        }) => Promise<RunStatus[]>;
        abort: (runId: string, reason?: string) => Promise<RunStatus>;
        complete: (runId: string) => Promise<RunStatus>;
        reset: (runId: string) => Promise<RunStatus>;
        setBudget: (runId: string, budgetUsd: number) => Promise<RunStatus>;
    };
    /**
     * Available models and price cards.
     */
    readonly models: {
        list: () => Promise<ModelInfo[]>;
    };
    /**
     * Current active project workspace balance and tier.
     */
    readonly project: {
        get: () => Promise<ProjectInfo>;
    };
    /**
     * Creates a custom fetch function bound to this SpendGuard gateway instance.
     */
    createFetch(options?: AgentRunOptions & {
        rewriteHost?: boolean;
    }): CustomFetch;
    /**
     * Wraps an OpenAI client instance with this SpendGuard client's gateway configuration.
     */
    wrapOpenAI<T extends {
        baseURL?: string;
        chat?: any;
        [key: string]: any;
    }>(openaiClient: T, options?: WrapOpenAIOptions): T;
}
//# sourceMappingURL=client.d.ts.map