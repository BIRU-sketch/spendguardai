/**
 * SpendGuard — Agent-Native Hard Cost Control SDK for Node.js and TypeScript.
 *
 * @packageDocumentation
 */

export { SpendGuard } from "./client.js";
export { SpendGuardRun } from "./run.js";
export {
  createSpendGuardFetch,
  spendguardFetch,
  extractTelemetryFromHeaders,
} from "./proxy/fetch.js";
export {
  wrapOpenAI,
  WrapOpenAIOptions,
  SpendGuardControl,
} from "./wrappers/openai.js";

export {
  SpendGuardError,
  AuthenticationError,
  BudgetExceededError,
  RunawayLoopError,
  RunAbortedError,
  ProviderError,
  APIError,
  parseSpendGuardError,
} from "./errors.js";

export {
  TokenUsage,
  SpendGuardTelemetry,
  ChatMessage,
  ChatCompletionOptions,
  ChatResponse,
  RunStatus,
  SpendGuardClientConfig,
  AgentRunOptions,
  ModelInfo,
  ModelPricing,
  ProjectInfo,
  SpendGuardHeaders,
  CustomFetch,
  SpendGuardFetchOptions,
} from "./types.js";

// Default export
import { SpendGuard } from "./client.js";
export default SpendGuard;
