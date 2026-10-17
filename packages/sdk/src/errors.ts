/**
 * SpendGuard SDK — Typed Exceptions.
 *
 * Provides strongly-typed error hierarchy for agent cost control,
 * budget breaches, runaway loops, and operator kill switches.
 */

export class SpendGuardError extends Error {
  public readonly statusCode?: number;
  public readonly details: Record<string, any>;

  constructor(message: string, statusCode?: number, details?: Record<string, any>) {
    super(message);
    this.name = "SpendGuardError";
    this.statusCode = statusCode;
    this.details = details || {};
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class AuthenticationError extends SpendGuardError {
  constructor(message = "SpendGuard API key invalid, missing, or revoked", details?: Record<string, any>) {
    super(message, 401, details);
    this.name = "AuthenticationError";
  }
}

export class BudgetExceededError extends SpendGuardError {
  public readonly scope: string;
  public readonly reason: string;
  public readonly runId?: string;
  public readonly spentCents: number;
  public readonly budgetCents?: number;
  public readonly projectRemainingCents?: number;

  constructor(params: {
    message: string;
    scope?: string;
    reason?: string;
    runId?: string;
    spentCents?: number;
    budgetCents?: number;
    projectRemainingCents?: number;
    details?: Record<string, any>;
  }) {
    super(params.message, 429, params.details);
    this.name = "BudgetExceededError";
    this.scope = params.scope || "run_budget";
    this.reason = params.reason || "run_budget_exhausted";
    this.runId = params.runId;
    this.spentCents = params.spentCents ?? 0;
    this.budgetCents = params.budgetCents;
    this.projectRemainingCents = params.projectRemainingCents;
  }

  get spentUsd(): number {
    return this.spentCents / 100;
  }

  get budgetUsd(): number | undefined {
    return this.budgetCents !== undefined ? this.budgetCents / 100 : undefined;
  }
}

export class RunawayLoopError extends SpendGuardError {
  public readonly reason: string;
  public readonly runId?: string;
  public readonly stepCount: number;

  constructor(params: {
    message: string;
    reason?: string;
    runId?: string;
    stepCount?: number;
    details?: Record<string, any>;
  }) {
    super(params.message, 429, params.details);
    this.name = "RunawayLoopError";
    this.reason = params.reason || "runaway_loop";
    this.runId = params.runId;
    this.stepCount = params.stepCount ?? 0;
  }
}

export class RunAbortedError extends SpendGuardError {
  public readonly runId?: string;
  public readonly reason: string;

  constructor(params: {
    message: string;
    runId?: string;
    reason?: string;
    details?: Record<string, any>;
  }) {
    super(params.message, 429, params.details);
    this.name = "RunAbortedError";
    this.runId = params.runId;
    this.reason = params.reason || "manual_abort";
  }
}

export class ProviderError extends SpendGuardError {
  public readonly provider?: string;

  constructor(message: string, statusCode = 502, provider?: string, details?: Record<string, any>) {
    super(message, statusCode, details);
    this.name = "ProviderError";
    this.provider = provider;
  }
}

export class APIError extends SpendGuardError {
  constructor(message: string, statusCode?: number, details?: Record<string, any>) {
    super(message, statusCode, details);
    this.name = "APIError";
  }
}

/**
 * Parses SpendGuard gateway error responses into typed exceptions.
 */
export function parseSpendGuardError(
  status: number,
  body: any,
  fallbackMessage = "SpendGuard request failed",
): SpendGuardError {
  const errData = body?.error || body?.detail?.error || body?.detail || body;
  const message = errData?.message || (typeof body === "string" ? body : fallbackMessage);
  const reason = errData?.reason || body?.reason;
  const scope = errData?.scope || body?.scope;
  const runId = errData?.run_id || body?.run_id;

  if (status === 401) {
    return new AuthenticationError(message, typeof body === "object" ? body : undefined);
  }

  if (status === 429) {
    if (scope === "runaway_loop" || reason?.includes("runaway") || reason === "max_steps_exceeded") {
      return new RunawayLoopError({
        message,
        reason,
        runId,
        stepCount: errData?.step_count ?? body?.step_count ?? 0,
        details: typeof body === "object" ? body : undefined,
      });
    }

    if (reason === "run_aborted" || reason?.includes("abort")) {
      return new RunAbortedError({
        message,
        runId,
        reason,
        details: typeof body === "object" ? body : undefined,
      });
    }

    // Default 429 is budget ceiling breach
    return new BudgetExceededError({
      message,
      scope,
      reason,
      runId,
      spentCents: errData?.run_spent_cents ?? body?.run_spent_cents ?? 0,
      budgetCents: errData?.run_budget_cents ?? body?.run_budget_cents,
      projectRemainingCents: errData?.project_remaining_cents ?? body?.project_remaining_cents,
      details: typeof body === "object" ? body : undefined,
    });
  }

  if (status === 502 || status === 504 || errData?.type === "upstream_provider_error") {
    return new ProviderError(message, status, errData?.provider, typeof body === "object" ? body : undefined);
  }

  return new APIError(message, status, typeof body === "object" ? body : undefined);
}
