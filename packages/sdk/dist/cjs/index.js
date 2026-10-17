"use strict";
/**
 * SpendGuard — Agent-Native Hard Cost Control SDK for Node.js and TypeScript.
 *
 * @packageDocumentation
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.SpendGuardHeaders = exports.parseSpendGuardError = exports.APIError = exports.ProviderError = exports.RunAbortedError = exports.RunawayLoopError = exports.BudgetExceededError = exports.AuthenticationError = exports.SpendGuardError = exports.wrapOpenAI = exports.extractTelemetryFromHeaders = exports.spendguardFetch = exports.createSpendGuardFetch = exports.SpendGuardRun = exports.SpendGuard = void 0;
var client_js_1 = require("./client.js");
Object.defineProperty(exports, "SpendGuard", { enumerable: true, get: function () { return client_js_1.SpendGuard; } });
var run_js_1 = require("./run.js");
Object.defineProperty(exports, "SpendGuardRun", { enumerable: true, get: function () { return run_js_1.SpendGuardRun; } });
var fetch_js_1 = require("./proxy/fetch.js");
Object.defineProperty(exports, "createSpendGuardFetch", { enumerable: true, get: function () { return fetch_js_1.createSpendGuardFetch; } });
Object.defineProperty(exports, "spendguardFetch", { enumerable: true, get: function () { return fetch_js_1.spendguardFetch; } });
Object.defineProperty(exports, "extractTelemetryFromHeaders", { enumerable: true, get: function () { return fetch_js_1.extractTelemetryFromHeaders; } });
var openai_js_1 = require("./wrappers/openai.js");
Object.defineProperty(exports, "wrapOpenAI", { enumerable: true, get: function () { return openai_js_1.wrapOpenAI; } });
var errors_js_1 = require("./errors.js");
Object.defineProperty(exports, "SpendGuardError", { enumerable: true, get: function () { return errors_js_1.SpendGuardError; } });
Object.defineProperty(exports, "AuthenticationError", { enumerable: true, get: function () { return errors_js_1.AuthenticationError; } });
Object.defineProperty(exports, "BudgetExceededError", { enumerable: true, get: function () { return errors_js_1.BudgetExceededError; } });
Object.defineProperty(exports, "RunawayLoopError", { enumerable: true, get: function () { return errors_js_1.RunawayLoopError; } });
Object.defineProperty(exports, "RunAbortedError", { enumerable: true, get: function () { return errors_js_1.RunAbortedError; } });
Object.defineProperty(exports, "ProviderError", { enumerable: true, get: function () { return errors_js_1.ProviderError; } });
Object.defineProperty(exports, "APIError", { enumerable: true, get: function () { return errors_js_1.APIError; } });
Object.defineProperty(exports, "parseSpendGuardError", { enumerable: true, get: function () { return errors_js_1.parseSpendGuardError; } });
var types_js_1 = require("./types.js");
Object.defineProperty(exports, "SpendGuardHeaders", { enumerable: true, get: function () { return types_js_1.SpendGuardHeaders; } });
// Default export
const client_js_2 = require("./client.js");
exports.default = client_js_2.SpendGuard;
//# sourceMappingURL=index.js.map