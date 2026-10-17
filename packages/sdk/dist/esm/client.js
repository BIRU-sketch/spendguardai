/**
 * SpendGuard — Main TypeScript Client.
 *
 * Client for managing agent runs, executing budget-controlled completions,
 * wrapping upstream SDKs, and generating fetch proxies.
 */
import { parseSpendGuardError } from "./errors.js";
import { createSpendGuardFetch, extractTelemetryFromHeaders } from "./proxy/fetch.js";
import { SpendGuardRun } from "./run.js";
import { SpendGuardHeaders, } from "./types.js";
import { wrapOpenAI } from "./wrappers/openai.js";
export class SpendGuard {
    apiKey;
    baseUrl;
    timeout;
    defaultBudget;
    defaultMaxSteps;
    _customFetch;
    chatCompletions;
    constructor(config = {}) {
        this.apiKey =
            config.apiKey ||
                (typeof process !== "undefined"
                    ? process.env?.SPENDGUARD_API_KEY || process.env?.ADMIN_API_KEY || process.env?.GATEWAY_API_KEY
                    : undefined) ||
                "";
        this.baseUrl = (config.baseUrl ||
            (typeof process !== "undefined" ? process.env?.SPENDGUARD_BASE_URL || process.env?.SPENDGUARD_GATEWAY_URL : undefined) ||
            "http://localhost:8000").replace(/\/+$/, "");
        this.timeout = config.timeout || 60000;
        this.defaultBudget = config.defaultBudget;
        this.defaultMaxSteps = config.defaultMaxSteps;
        // OpenAI compatibility proxy: client.chat.completions.create(...)
        this.chatCompletions = {
            create: (body, options) => this.chat({ ...body, ...(options?.spendguard || {}) }),
        };
    }
    get completions() {
        return this.chatCompletions;
    }
    async request(path, init) {
        const fetchFn = this._customFetch || globalThis.fetch;
        const url = `${this.baseUrl}${path}`;
        const headers = new Headers(init?.headers);
        if (!headers.has("Authorization") && this.apiKey) {
            headers.set("Authorization", `Bearer ${this.apiKey}`);
        }
        if (!headers.has("Content-Type") && init?.method && init.method !== "GET") {
            headers.set("Content-Type", "application/json");
        }
        const res = await fetchFn(url, { ...init, headers });
        const text = await res.text();
        let body;
        try {
            body = text ? JSON.parse(text) : {};
        }
        catch {
            body = text;
        }
        if (!res.ok) {
            throw parseSpendGuardError(res.status, body);
        }
        return body;
    }
    /**
     * Executes a chat completion bound by agent spend limits and runaway protection.
     */
    async chat(options) {
        const fetchFn = this._customFetch || globalThis.fetch;
        const path = "/v1/chat/completions";
        const url = `${this.baseUrl}${path}`;
        const headers = new Headers(options.extraHeaders);
        if (this.apiKey) {
            headers.set("Authorization", `Bearer ${this.apiKey}`);
        }
        headers.set("Content-Type", "application/json");
        if (options.runId)
            headers.set(SpendGuardHeaders.RUN_ID, options.runId);
        if (options.agentId)
            headers.set(SpendGuardHeaders.AGENT_ID, options.agentId);
        if (options.sessionId)
            headers.set(SpendGuardHeaders.SESSION_ID, options.sessionId);
        const budgetCents = options.budgetCents !== undefined
            ? options.budgetCents
            : options.budget !== undefined
                ? Math.round(options.budget * 100)
                : this.defaultBudget !== undefined
                    ? Math.round(this.defaultBudget * 100)
                    : undefined;
        if (budgetCents !== undefined) {
            headers.set(SpendGuardHeaders.RUN_BUDGET_CENTS, String(budgetCents));
        }
        const maxSteps = options.maxSteps ?? this.defaultMaxSteps;
        if (maxSteps !== undefined) {
            headers.set(SpendGuardHeaders.MAX_STEPS, String(maxSteps));
        }
        if (options.provider) {
            headers.set(SpendGuardHeaders.PROVIDER, options.provider);
        }
        const normalizedMessages = typeof options.messages === "string"
            ? [{ role: "user", content: options.messages }]
            : options.messages;
        const payload = {
            model: options.model,
            messages: normalizedMessages,
            stream: Boolean(options.stream),
            max_tokens: options.maxTokens,
            temperature: options.temperature,
            tools: options.tools,
            tool_choice: options.toolChoice,
        };
        const res = await fetchFn(url, {
            method: "POST",
            headers,
            body: JSON.stringify(payload),
        });
        if (!res.ok) {
            const errText = await res.text();
            let errBody;
            try {
                errBody = JSON.parse(errText);
            }
            catch {
                errBody = errText;
            }
            throw parseSpendGuardError(res.status, errBody);
        }
        const telemetry = extractTelemetryFromHeaders(res.headers);
        // Streaming handling
        if (options.stream) {
            const reader = res.body?.getReader();
            if (!reader) {
                throw new Error("Response body is not readable for streaming");
            }
            const decoder = new TextDecoder();
            return (async function* () {
                let buffer = "";
                try {
                    while (true) {
                        const { done, value } = await reader.read();
                        if (done)
                            break;
                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split("\n");
                        buffer = lines.pop() || "";
                        for (const line of lines) {
                            const trimmed = line.trim();
                            if (trimmed.startsWith("data: ")) {
                                const data = trimmed.slice(6);
                                if (data === "[DONE]")
                                    return;
                                try {
                                    yield JSON.parse(data);
                                }
                                catch {
                                    // ignore keepalives or unparseable text
                                }
                            }
                        }
                    }
                }
                finally {
                    reader.releaseLock();
                }
            })();
        }
        // Non-streaming handling
        const data = await res.json();
        const choice = data.choices?.[0];
        const rawUsage = data.usage || {};
        const usage = {
            promptTokens: rawUsage.prompt_tokens || 0,
            completionTokens: rawUsage.completion_tokens || 0,
            totalTokens: rawUsage.total_tokens || ((rawUsage.prompt_tokens || 0) + (rawUsage.completion_tokens || 0)),
            cachedTokens: rawUsage.prompt_tokens_details?.cached_tokens,
            reasoningTokens: rawUsage.completion_tokens_details?.reasoning_tokens,
        };
        return {
            content: choice?.message?.content ?? null,
            model: data.model || options.model,
            finishReason: choice?.finish_reason,
            usage,
            toolCalls: choice?.message?.tool_calls,
            spendguard: telemetry,
            raw: data,
        };
    }
    /**
     * Initializes and registers an autonomous agent run trajectory.
     */
    async run(options = {}) {
        const runInstance = new SpendGuardRun({
            ...options,
            budget: options.budget ?? this.defaultBudget,
            maxSteps: options.maxSteps ?? this.defaultMaxSteps,
        }, {
            baseUrl: this.baseUrl,
            apiKey: this.apiKey,
            timeout: this.timeout,
            fetch: this._customFetch,
        });
        await runInstance.init();
        return runInstance;
    }
    /**
     * Agent run trajectory management endpoints.
     */
    runs = {
        get: async (runId) => {
            const res = await this.request(`/v1/runs/${runId}`);
            const r = res.run;
            const spentCents = r.spent_cents ?? 0;
            const budgetCents = r.budget_cents;
            const remCents = r.remaining_cents ?? (budgetCents !== undefined ? Math.max(0, budgetCents - spentCents) : undefined);
            return {
                runId: r.run_id,
                projectId: r.project_id,
                agentId: r.agent_id,
                sessionId: r.session_id,
                budgetCents,
                budgetUsd: budgetCents !== undefined ? budgetCents / 100 : undefined,
                spentCents,
                spentUsd: spentCents / 100,
                remainingCents: remCents,
                remainingUsd: remCents !== undefined ? remCents / 100 : undefined,
                stepCount: r.step_count ?? 0,
                isAborted: r.status === "aborted" || r.status === "runaway_aborted",
                status: r.status || "active",
                metadata: r.metadata || {},
            };
        },
        list: async (params) => {
            const query = new URLSearchParams();
            if (params?.agentId)
                query.set("agent_id", params.agentId);
            if (params?.status)
                query.set("status", params.status);
            if (params?.limit)
                query.set("limit", String(params.limit));
            if (params?.offset)
                query.set("offset", String(params.offset));
            const res = await this.request(`/v1/runs?${query.toString()}`);
            return (res.runs || []).map((r) => {
                const spentCents = r.spent_cents ?? 0;
                const budgetCents = r.budget_cents;
                const remCents = r.remaining_cents ?? (budgetCents !== undefined ? Math.max(0, budgetCents - spentCents) : undefined);
                return {
                    runId: r.run_id,
                    projectId: r.project_id,
                    agentId: r.agent_id,
                    sessionId: r.session_id,
                    budgetCents,
                    budgetUsd: budgetCents !== undefined ? budgetCents / 100 : undefined,
                    spentCents,
                    spentUsd: spentCents / 100,
                    remainingCents: remCents,
                    remainingUsd: remCents !== undefined ? remCents / 100 : undefined,
                    stepCount: r.step_count ?? 0,
                    isAborted: r.status === "aborted" || r.status === "runaway_aborted",
                    status: r.status || "active",
                    metadata: r.metadata || {},
                };
            });
        },
        abort: async (runId, reason = "manual_abort") => {
            const res = await this.request(`/v1/runs/${runId}/abort`, {
                method: "POST",
                body: JSON.stringify({ reason }),
            });
            return this.runs.get(runId);
        },
        complete: async (runId) => {
            await this.request(`/v1/runs/${runId}/complete`, {
                method: "POST",
            });
            return this.runs.get(runId);
        },
        reset: async (runId) => {
            await this.request(`/v1/runs/${runId}/reset`, {
                method: "POST",
            });
            return this.runs.get(runId);
        },
        setBudget: async (runId, budgetUsd) => {
            const budgetCents = Math.round(budgetUsd * 100);
            await this.request(`/v1/runs/${runId}/budget`, {
                method: "POST",
                body: JSON.stringify({ budget_cents: budgetCents }),
            });
            return this.runs.get(runId);
        },
    };
    /**
     * Available models and price cards.
     */
    models = {
        list: async () => {
            const res = await this.request("/v1/models");
            return (res.data || []).map((m) => ({
                id: m.id,
                ownedBy: m.owned_by,
                pricing: {
                    inputCentsPer1k: m.pricing?.input_cents_per_1k || 0,
                    outputCentsPer1k: m.pricing?.output_cents_per_1k || 0,
                    currency: m.pricing?.currency || "USD",
                },
            }));
        },
    };
    /**
     * Current active project workspace balance and tier.
     */
    project = {
        get: async () => {
            const res = await this.request("/project");
            return {
                projectId: res.project.project_id,
                name: res.project.name,
                tier: res.project.tier,
                balanceCents: res.balance_cents || 0,
                balanceUsd: (res.balance_cents || 0) / 100,
                currency: res.currency || "USD",
            };
        },
    };
    /**
     * Creates a custom fetch function bound to this SpendGuard gateway instance.
     */
    createFetch(options = {}) {
        return createSpendGuardFetch({
            gatewayUrl: this.baseUrl,
            apiKey: this.apiKey,
            runId: options.runId,
            agentId: options.agentId,
            sessionId: options.sessionId,
            budget: options.budget ?? this.defaultBudget,
            budgetCents: options.budgetCents,
            maxSteps: options.maxSteps ?? this.defaultMaxSteps,
            provider: options.provider,
            rewriteHost: options.rewriteHost !== false,
            fetch: this._customFetch,
        });
    }
    /**
     * Wraps an OpenAI client instance with this SpendGuard client's gateway configuration.
     */
    wrapOpenAI(openaiClient, options = {}) {
        return wrapOpenAI(openaiClient, {
            gatewayUrl: this.baseUrl,
            apiKey: this.apiKey,
            budget: this.defaultBudget,
            maxSteps: this.defaultMaxSteps,
            ...options,
        });
    }
}
//# sourceMappingURL=client.js.map