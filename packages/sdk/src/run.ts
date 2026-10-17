/**
 * SpendGuard — Agent Run Trajectory Context.
 *
 * Tracks cumulative cost, step iteration, remaining budget,
 * and lifecycle events for an autonomous agent execution.
 */

import { parseSpendGuardError } from "./errors.js";
import { createSpendGuardFetch } from "./proxy/fetch.js";
import {
  AgentRunOptions,
  ChatCompletionOptions,
  ChatResponse,
  CustomFetch,
  RunStatus,
  SpendGuardHeaders,
} from "./types.js";
import { wrapOpenAI } from "./wrappers/openai.js";

export interface RunManagerConfig {
  baseUrl: string;
  apiKey: string;
  timeout?: number;
  fetch?: CustomFetch;
}

export class SpendGuardRun {
  private readonly config: RunManagerConfig;
  public readonly runId: string;
  public readonly agentId?: string;
  public readonly sessionId?: string;
  public budgetUsd?: number;
  public maxSteps?: number;
  public metadata: Record<string, any>;
  public autoComplete: boolean;

  public spentUsd = 0;
  public remainingUsd?: number;
  public step = 0;
  public status = "active";
  public isAborted = false;

  constructor(options: AgentRunOptions, config: RunManagerConfig) {
    this.config = config;
    this.runId = options.runId || `run_${Math.random().toString(36).slice(2, 11)}`;
    this.agentId = options.agentId;
    this.sessionId = options.sessionId;
    this.budgetUsd = options.budget !== undefined ? options.budget : options.budgetCents !== undefined ? options.budgetCents / 100 : undefined;
    this.maxSteps = options.maxSteps;
    this.metadata = options.metadata || {};
    this.autoComplete = options.autoComplete !== false;
    this.remainingUsd = this.budgetUsd;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const fetchFn = this.config.fetch || globalThis.fetch;
    const url = `${this.config.baseUrl.replace(/\/+$/, "")}${path}`;

    const headers = new Headers(init?.headers);
    if (!headers.has("Authorization") && this.config.apiKey) {
      headers.set("Authorization", `Bearer ${this.config.apiKey}`);
    }
    if (!headers.has("Content-Type") && init?.method && init.method !== "GET") {
      headers.set("Content-Type", "application/json");
    }

    const res = await fetchFn(url, { ...init, headers });
    const text = await res.text();
    let body: any;
    try {
      body = text ? JSON.parse(text) : {};
    } catch {
      body = text;
    }

    if (!res.ok) {
      throw parseSpendGuardError(res.status, body);
    }

    return body as T;
  }

  /**
   * Registers the run trajectory with the SpendGuard gateway.
   */
  async init(): Promise<this> {
    const budgetCents = this.budgetUsd !== undefined ? Math.round(this.budgetUsd * 100) : undefined;
    const body = await this.request<{ status: string; run: any }>("/v1/runs", {
      method: "POST",
      body: JSON.stringify({
        run_id: this.runId,
        agent_id: this.agentId,
        session_id: this.sessionId,
        budget_cents: budgetCents,
        max_steps: this.maxSteps,
        metadata: this.metadata,
      }),
    });

    if (body?.run) {
      this.applyStatus(body.run);
    }
    return this;
  }

  /**
   * Refreshes run spend and step metrics from the gateway.
   */
  async refresh(): Promise<RunStatus> {
    const body = await this.request<{ status: string; run: any }>(`/v1/runs/${this.runId}`);
    return this.applyStatus(body.run);
  }

  /**
   * Dynamically assigns or updates the spend ceiling in USD.
   */
  async setBudget(budgetUsd: number): Promise<RunStatus> {
    const budgetCents = Math.round(budgetUsd * 100);
    const body = await this.request<{ status: string; run: any }>(`/v1/runs/${this.runId}/budget`, {
      method: "POST",
      body: JSON.stringify({ budget_cents: budgetCents }),
    });
    this.budgetUsd = budgetUsd;
    return this.applyStatus(body.run);
  }

  /**
   * Marks this trajectory completed.
   */
  async complete(): Promise<RunStatus> {
    const body = await this.request<{ status: string; run: any }>(`/v1/runs/${this.runId}/complete`, {
      method: "POST",
    });
    return this.applyStatus(body.run);
  }

  /**
   * Emergency circuit breaker: immediately halts this run.
   */
  async abort(reason = "manual_abort"): Promise<RunStatus> {
    const body = await this.request<{ status: string; run: any }>(`/v1/runs/${this.runId}/abort`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    return this.applyStatus(body.run);
  }

  /**
   * Resets runaway circuit breakers and step counters for this run.
   */
  async reset(): Promise<RunStatus> {
    const body = await this.request<{ status: string; run: any }>(`/v1/runs/${this.runId}/reset`, {
      method: "POST",
    });
    return this.applyStatus(body.run);
  }

  /**
   * Creates a custom fetch bound to this specific agent run trajectory.
   */
  createFetch(extraOptions: { rewriteHost?: boolean } = {}): CustomFetch {
    return createSpendGuardFetch({
      gatewayUrl: this.config.baseUrl,
      apiKey: this.config.apiKey,
      runId: this.runId,
      agentId: this.agentId,
      sessionId: this.sessionId,
      budget: this.budgetUsd,
      maxSteps: this.maxSteps,
      rewriteHost: extraOptions.rewriteHost !== false,
      fetch: this.config.fetch,
      onTelemetry: (t) => {
        if (t.runSpentUsd !== undefined) this.spentUsd = t.runSpentUsd;
        if (t.runRemainingUsd !== undefined) this.remainingUsd = t.runRemainingUsd;
        if (t.step !== undefined) this.step = t.step;
      },
    });
  }

  /**
   * Wraps an OpenAI client instance bound to this agent trajectory.
   */
  wrapOpenAI<T extends { baseURL?: string; chat?: any; [key: string]: any }>(client: T): T {
    return wrapOpenAI(client, {
      gatewayUrl: this.config.baseUrl,
      apiKey: this.config.apiKey,
      runId: this.runId,
      agentId: this.agentId,
      sessionId: this.sessionId,
      budget: this.budgetUsd,
      maxSteps: this.maxSteps,
    });
  }

  /**
   * Normalizes raw run payload to strongly typed RunStatus.
   */
  private applyStatus(data: any): RunStatus {
    const spentCents = data.spent_cents ?? 0;
    const budgetCents = data.budget_cents ?? (this.budgetUsd ? Math.round(this.budgetUsd * 100) : undefined);
    const remCents = data.remaining_cents ?? (budgetCents !== undefined ? Math.max(0, budgetCents - spentCents) : undefined);

    this.spentUsd = spentCents / 100;
    this.remainingUsd = remCents !== undefined ? remCents / 100 : undefined;
    this.step = data.step_count ?? 0;
    this.status = data.status || "active";
    this.isAborted = data.status === "aborted" || data.status === "runaway_aborted";

    return {
      runId: data.run_id || this.runId,
      projectId: data.project_id || "",
      agentId: data.agent_id || this.agentId,
      sessionId: data.session_id || this.sessionId,
      budgetCents,
      budgetUsd: budgetCents !== undefined ? budgetCents / 100 : undefined,
      spentCents,
      spentUsd: this.spentUsd,
      remainingCents: remCents,
      remainingUsd: this.remainingUsd,
      stepCount: this.step,
      isAborted: this.isAborted,
      status: this.status,
      metadata: data.metadata || {},
    };
  }
}
