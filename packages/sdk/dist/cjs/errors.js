"use strict";
/**
 * SpendGuard SDK — Typed Exceptions.
 *
 * Provides strongly-typed error hierarchy for agent cost control,
 * budget breaches, runaway loops, and operator kill switches.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.APIError = exports.ProviderError = exports.RunAbortedError = exports.RunawayLoopError = exports.BudgetExceededError = exports.AuthenticationError = exports.SpendGuardError = void 0;
exports.parseSpendGuardError = parseSpendGuardError;
class SpendGuardError extends Error {
    statusCode;
    details;
    constructor(message, statusCode, details) {
        super(message);
        this.name = "SpendGuardError";
        this.statusCode = statusCode;
        this.details = details || {};
        Object.setPrototypeOf(this, new.target.prototype);
    }
}
exports.SpendGuardError = SpendGuardError;
class AuthenticationError extends SpendGuardError {
    constructor(message = "SpendGuard API key invalid, missing, or revoked", details) {
        super(message, 401, details);
        this.name = "AuthenticationError";
    }
}
exports.AuthenticationError = AuthenticationError;
class BudgetExceededError extends SpendGuardError {
    scope;
    reason;
    runId;
    spentCents;
    budgetCents;
    projectRemainingCents;
    constructor(params) {
        super(params.message, 429, params.details);
        this.name = "BudgetExceededError";
        this.scope = params.scope || "run_budget";
        this.reason = params.reason || "run_budget_exhausted";
        this.runId = params.runId;
        this.spentCents = params.spentCents ?? 0;
        this.budgetCents = params.budgetCents;
        this.projectRemainingCents = params.projectRemainingCents;
    }
    get spentUsd() {
        return this.spentCents / 100;
    }
    get budgetUsd() {
        return this.budgetCents !== undefined ? this.budgetCents / 100 : undefined;
    }
}
exports.BudgetExceededError = BudgetExceededError;
class RunawayLoopError extends SpendGuardError {
    reason;
    runId;
    stepCount;
    constructor(params) {
        super(params.message, 429, params.details);
        this.name = "RunawayLoopError";
        this.reason = params.reason || "runaway_loop";
        this.runId = params.runId;
        this.stepCount = params.stepCount ?? 0;
    }
}
exports.RunawayLoopError = RunawayLoopError;
class RunAbortedError extends SpendGuardError {
    runId;
    reason;
    constructor(params) {
        super(params.message, 429, params.details);
        this.name = "RunAbortedError";
        this.runId = params.runId;
        this.reason = params.reason || "manual_abort";
    }
}
exports.RunAbortedError = RunAbortedError;
class ProviderError extends SpendGuardError {
    provider;
    constructor(message, statusCode = 502, provider, details) {
        super(message, statusCode, details);
        this.name = "ProviderError";
        this.provider = provider;
    }
}
exports.ProviderError = ProviderError;
class APIError extends SpendGuardError {
    constructor(message, statusCode, details) {
        super(message, statusCode, details);
        this.name = "APIError";
    }
}
exports.APIError = APIError;
/**
 * Parses SpendGuard gateway error responses into typed exceptions.
 */
function parseSpendGuardError(status, body, fallbackMessage = "SpendGuard request failed") {
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
//# sourceMappingURL=errors.js.map