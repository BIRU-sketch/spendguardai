/**
 * SpendGuard — Agent-Native Hard Cost Control SDK for Node.js and TypeScript.
 *
 * @packageDocumentation
 */
export { SpendGuard } from "./client.js";
export { SpendGuardRun } from "./run.js";
export { createSpendGuardFetch, spendguardFetch, extractTelemetryFromHeaders, } from "./proxy/fetch.js";
export { wrapOpenAI, } from "./wrappers/openai.js";
export { SpendGuardError, AuthenticationError, BudgetExceededError, RunawayLoopError, RunAbortedError, ProviderError, APIError, parseSpendGuardError, } from "./errors.js";
export { SpendGuardHeaders, } from "./types.js";
// Default export
import { SpendGuard } from "./client.js";
export default SpendGuard;
//# sourceMappingURL=index.js.map