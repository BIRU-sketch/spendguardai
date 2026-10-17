/**
 * SpendGuard SDK — Typed Exceptions.
 *
 * Provides strongly-typed error hierarchy for agent cost control,
 * budget breaches, runaway loops, and operator kill switches.
 */
export declare class SpendGuardError extends Error {
    readonly statusCode?: number;
    readonly details: Record<string, any>;
    constructor(message: string, statusCode?: number, details?: Record<string, any>);
}
export declare class AuthenticationError extends SpendGuardError {
    constructor(message?: string, details?: Record<string, any>);
}
export declare class BudgetExceededError extends SpendGuardError {
    readonly scope: string;
    readonly reason: string;
    readonly runId?: string;
    readonly spentCents: number;
    readonly budgetCents?: number;
    readonly projectRemainingCents?: number;
    constructor(params: {
        message: string;
        scope?: string;
        reason?: string;
        runId?: string;
        spentCents?: number;
        budgetCents?: number;
        projectRemainingCents?: number;
        details?: Record<string, any>;
    });
    get spentUsd(): number;
    get budgetUsd(): number | undefined;
}
export declare class RunawayLoopError extends SpendGuardError {
    readonly reason: string;
    readonly runId?: string;
    readonly stepCount: number;
    constructor(params: {
        message: string;
        reason?: string;
        runId?: string;
        stepCount?: number;
        details?: Record<string, any>;
    });
}
export declare class RunAbortedError extends SpendGuardError {
    readonly runId?: string;
    readonly reason: string;
    constructor(params: {
        message: string;
        runId?: string;
        reason?: string;
        details?: Record<string, any>;
    });
}
export declare class ProviderError extends SpendGuardError {
    readonly provider?: string;
    constructor(message: string, statusCode?: number, provider?: string, details?: Record<string, any>);
}
export declare class APIError extends SpendGuardError {
    constructor(message: string, statusCode?: number, details?: Record<string, any>);
}
/**
 * Parses SpendGuard gateway error responses into typed exceptions.
 */
export declare function parseSpendGuardError(status: number, body: any, fallbackMessage?: string): SpendGuardError;
//# sourceMappingURL=errors.d.ts.map