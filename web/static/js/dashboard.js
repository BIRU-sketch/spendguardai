/**
 * SpendGuard AI — Frontend Dashboard & Onboarding Controller
 *
 * Drives the lean post-signup UI:
 * - 3-step Onboarding Checklist with simulated protected call
 * - 3 Dashboard Tabs: API Keys, Runs, Usage
 * - Dark/Light Neo-Brutalist Theme Controller
 * - Responsive Drawer & Show-Once Key Security Flow
 */

import * as api from "./api.js";

// ============================================================================
// State Management
// ============================================================================
const state = {
  currentTab: "keys",
  keys: [],
  runs: [],
  runsFilter: "all",
  runsPage: 1,
  runsTotalPages: 1,
  usagePeriod: "7d",
  onboarding: null,
  activeRawKey: null, // Only held while show-once modal is open
  selectedRun: null,
  keyToRevoke: null,
  activeSnippetLang: "python",
};

// ============================================================================
// Theme Management
// ============================================================================
function initTheme() {
  const saved = localStorage.getItem("sg_theme");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const theme = saved || (prefersDark ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", theme);
  updateThemeIcon(theme);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "light";
  const next = current === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("sg_theme", next);
  updateThemeIcon(next);
}

function updateThemeIcon(theme) {
  const btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.textContent = theme === "dark" ? "☀️ Light" : "🌙 Dark";
    btn.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} mode`);
  }
}

// ============================================================================
// Toast & Modal Helpers
// ============================================================================
export function showToast(message) {
  let toast = document.getElementById("toast-element");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "toast-element";
    toast.className = "toast-msg";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => {
    toast.classList.remove("show");
  }, 2600);
}

function copyText(text, successMsg = "Copied to clipboard") {
  if (!text) return;
  navigator.clipboard.writeText(text).then(
    () => showToast(successMsg),
    () => {
      // Fallback
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      showToast(successMsg);
    }
  );
}

function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.add("open");
    // Trap focus inside modal
    const focusable = modal.querySelector("button, input, select, textarea, [tabindex]:not([tabindex='-1'])");
    if (focusable) focusable.focus();
  }
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.remove("open");
}

function openDrawer(run) {
  state.selectedRun = run;
  renderRunDrawer(run);
  const drawer = document.getElementById("run-drawer");
  if (drawer) drawer.classList.add("open");
}

function closeDrawer() {
  const drawer = document.getElementById("run-drawer");
  if (drawer) drawer.classList.remove("open");
  state.selectedRun = null;
}

// Escape key to dismiss modals & drawers
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.querySelectorAll(".modal-overlay.open").forEach((m) => m.classList.remove("open"));
    closeDrawer();
  }
});

// ============================================================================
// Onboarding Controller
// ============================================================================
async function initOnboarding() {
  try {
    state.onboarding = await api.getOnboardingState();
    renderOnboarding();
  } catch (err) {
    console.error("Failed to load onboarding state:", err);
  }
}

function renderOnboarding() {
  const container = document.getElementById("onboarding-section");
  if (!container) return;

  const ob = state.onboarding || {};
  const step1Done = ob.step1_created_key;
  const step2Done = ob.step2_viewed_code;
  const step3Done = ob.step3_tested_call;
  const isAllDone = step1Done && step2Done && step3Done;

  const activeKeyPlaceholder = state.activeRawKey || ob.last_created_key || "<your-spendguard-api-key>";

  container.innerHTML = `
    <div class="onboarding-card">
      <div class="onboarding-card-header">
        <div>
          <span class="onboarding-badge">${isAllDone ? "✓ Complete" : "Quickstart"}</span>
          <h2 style="font-size: 1.25rem; margin-top: 0.35rem;">First Protected Call in 5 Minutes</h2>
          <p class="step-body">Follow these 3 steps to lock your agent spend ceiling before any tokens are billed.</p>
        </div>
        <button id="dismiss-onboarding-btn" class="btn btn-sm" title="Minimize Setup Guide">
          ${isAllDone ? "Hide Guide" : "Collapse"}
        </button>
      </div>

      <div class="onboarding-steps">
        <!-- Step 1: Create Key -->
        <div class="step-item ${step1Done ? "completed" : ""}" id="onboarding-step-1">
          <div class="step-header">
            <div class="step-title-group">
              <div class="step-number">${step1Done ? "✓" : "1"}</div>
              <div class="step-title">Create your first API key</div>
            </div>
            <div class="step-status-tag">${step1Done ? "Ready" : "Step 1"}</div>
          </div>
          <p class="step-body">Generate an API key to authenticate your agent gateway calls.</p>
          <div class="step-action-row">
            <button id="onboarding-gen-key-btn" class="btn btn-primary btn-sm">
              ${step1Done ? "Create Another Key" : "+ Create API Key"}
            </button>
            ${step1Done ? `<span class="mono" style="font-size: 0.8rem; color: var(--color-green);">✓ Key initialized</span>` : ""}
          </div>
        </div>

        <!-- Step 2: Copy-Paste Example -->
        <div class="step-item ${step2Done ? "completed" : ""}" id="onboarding-step-2">
          <div class="step-header">
            <div class="step-title-group">
              <div class="step-number">${step2Done ? "✓" : "2"}</div>
              <div class="step-title">Copy integration code into your agent</div>
            </div>
            <div class="step-status-tag">${step2Done ? "Ready" : "Step 2"}</div>
          </div>
          <p class="step-body">Drop SpendGuard into your existing agent loop. Set a hard dollar budget and max steps limit.</p>

          <div class="code-box">
            <div class="code-box-header">
              <div class="lang-tabs">
                <button class="lang-tab ${state.activeSnippetLang === "python" ? "active" : ""}" data-lang="python">Python</button>
                <button class="lang-tab ${state.activeSnippetLang === "javascript" ? "active" : ""}" data-lang="javascript">JavaScript</button>
                <button class="lang-tab ${state.activeSnippetLang === "curl" ? "active" : ""}" data-lang="curl">cURL</button>
              </div>
              <button id="copy-onboarding-snippet-btn" class="code-copy-btn">Copy Snippet</button>
            </div>
            <pre class="code-pre" id="snippet-display-area"></pre>
          </div>

          <div class="step-action-row">
            <button id="mark-step2-done-btn" class="btn btn-sm ${step2Done ? "" : "btn-primary"}">
              ${step2Done ? "✓ Snippet Viewed" : "Mark as Integrated →"}
            </button>
          </div>
        </div>

        <!-- Step 3: Test Protected Call -->
        <div class="step-item ${step3Done ? "completed" : ""}" id="onboarding-step-3">
          <div class="step-header">
            <div class="step-title-group">
              <div class="step-number">${step3Done ? "✓" : "3"}</div>
              <div class="step-title">Test a protected call (Simulated)</div>
            </div>
            <div class="step-status-tag">${step3Done ? "Protected" : "Step 3"}</div>
          </div>
          <p class="step-body">Simulate an agent hitting its $1.50 hard limit. Watch SpendGuard block upstream calls before any unexpected bills occur.</p>

          <div class="step-action-row">
            <button id="run-sim-call-btn" class="btn btn-primary btn-sm">
              ▶ Run Simulated Protected Call
            </button>
            <span class="mono" style="font-size: 0.75rem; color: var(--color-text-muted);">
              (Returns 429 budget_exceeded &bull; $0.00 upstream billed)
            </span>
          </div>

          <div id="sim-console-output" style="display: none;"></div>
        </div>
      </div>

      ${
        isAllDone
          ? `
        <div class="onboarding-success-banner">
          <div>
            <strong>✓ Your first protected call is live!</strong>
            <p style="font-size: 0.82rem; margin-top: 0.15rem;">SpendGuard is actively shielding your agent trajectories.</p>
          </div>
          <button id="go-to-runs-btn" class="btn btn-primary btn-sm">View Runs Tab →</button>
        </div>
      `
          : ""
      }
    </div>
  `;

  renderSnippetCode(activeKeyPlaceholder);
  bindOnboardingEvents();
}

function renderSnippetCode(apiKey) {
  const pre = document.getElementById("snippet-display-area");
  if (!pre) return;

  const key = apiKey || "<your-spendguard-api-key>";
  const lang = state.activeSnippetLang;

  if (lang === "python") {
    pre.textContent = `from sg_run import SpendGuard

# 1. Initialize client
client = SpendGuard(api_key="${key}")

# 2. Lock trajectory budget ceiling ($1.50) & max iterations (25)
run = client.run(
    agent_id="support-agent",
    budget=1.50,
    max_steps=25
)

# 3. Execute bounded LLM call
response = client.chat(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Analyze customer issue"}],
    run_id=run.run_id
)
print(response.content)`;
  } else if (lang === "javascript") {
    pre.textContent = `import OpenAI from "openai";
import { wrapOpenAI } from "sg-run";

// Wrap your existing OpenAI client with SpendGuard guardrails
const openai = wrapOpenAI(new OpenAI({ apiKey: process.env.OPENAI_API_KEY }), {
  apiKey: "${key}",
  agentId: "support-agent",
  budget: 1.50, // $1.50 USD hard ceiling
  maxSteps: 25  // Tripwire limit
});

const completion = await openai.chat.completions.create({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Analyze customer issue" }]
});
console.log(completion.choices[0].message.content);`;
  } else {
    pre.textContent = `curl https://api.spendguard.ai/v1/chat/completions \\
  -H "Authorization: Bearer ${key}" \\
  -H "Content-Type: application/json" \\
  -H "X-SpendGuard-Agent-Id: support-agent" \\
  -H "X-SpendGuard-Run-Budget-Cents: 150" \\
  -H "X-SpendGuard-Max-Steps: 25" \\
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Analyze customer issue"}]
  }'`;
  }
}

function bindOnboardingEvents() {
  // Snippet lang tabs
  document.querySelectorAll(".lang-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.activeSnippetLang = btn.dataset.lang;
      document.querySelectorAll(".lang-tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const activeKeyPlaceholder = state.activeRawKey || (state.onboarding && state.onboarding.last_created_key);
      renderSnippetCode(activeKeyPlaceholder);
    });
  });

  // Copy snippet button
  const copyBtn = document.getElementById("copy-onboarding-snippet-btn");
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      const pre = document.getElementById("snippet-display-area");
      if (pre) {
        copyText(pre.textContent, "Snippet copied to clipboard");
        await api.setOnboardingStep(2, true);
        state.onboarding = await api.getOnboardingState();
        renderOnboarding();
      }
    });
  }

  // Mark step 2 done button
  const markStep2Btn = document.getElementById("mark-step2-done-btn");
  if (markStep2Btn) {
    markStep2Btn.addEventListener("click", async () => {
      await api.setOnboardingStep(2, true);
      state.onboarding = await api.getOnboardingState();
      renderOnboarding();
    });
  }

  // Generate Key from Onboarding
  const genKeyBtn = document.getElementById("onboarding-gen-key-btn");
  if (genKeyBtn) {
    genKeyBtn.addEventListener("click", () => {
      openCreateKeyModal();
    });
  }

  // Run Simulated Call
  const simBtn = document.getElementById("run-sim-call-btn");
  if (simBtn) {
    simBtn.addEventListener("click", handleRunSimulatedCall);
  }

  // Dismiss onboarding
  const dismissBtn = document.getElementById("dismiss-onboarding-btn");
  if (dismissBtn) {
    dismissBtn.addEventListener("click", () => {
      const card = document.getElementById("onboarding-section");
      if (card) card.style.display = "none";
    });
  }

  // Go to runs tab
  const goToRunsBtn = document.getElementById("go-to-runs-btn");
  if (goToRunsBtn) {
    goToRunsBtn.addEventListener("click", () => {
      switchTab("runs");
    });
  }
}

async function handleRunSimulatedCall() {
  const btn = document.getElementById("run-sim-call-btn");
  const consoleEl = document.getElementById("sim-console-output");
  if (!btn || !consoleEl) return;

  btn.disabled = true;
  btn.textContent = "⏳ Running simulated call...";
  consoleEl.style.display = "block";
  consoleEl.innerHTML = `
    <div class="sim-console">
      <div class="sim-header">
        <span class="sim-tag">GATEWAY SIMULATION</span>
        <span class="mono" style="color: #9ca3af;">Testing pre-flight reservation & hard cap...</span>
      </div>
      <div class="sim-line dim">→ Dispatching: POST /v1/chat/completions (agent_id='quickstart-agent', budget=$1.50)</div>
      <div class="sim-line dim">→ Redis atomic reservation checked: run_spent reached 150 cents...</div>
    </div>
  `;

  try {
    const result = await api.runTestCall({
      apiKey: state.activeRawKey || "sk-live-simulated-key",
      model: "gpt-4o",
      budgetUsd: 1.50,
      maxSteps: 25,
    });

    consoleEl.innerHTML = `
      <div class="sim-console">
        <div class="sim-header">
          <span class="sim-tag">GATEWAY SIMULATION (RESULT)</span>
          <span class="sim-status-code">HTTP 429 Too Many Requests</span>
        </div>
        <div class="sim-line red">✕ Tripwire: ${result.headers["X-SpendGuard-Tripwire"]}</div>
        <div class="sim-line green">✓ Upstream Tokens Billed: $0.00 (Blocked pre-flight)</div>
        <div class="sim-line dim">------------------------------------------------------------</div>
        <div class="sim-line dim">Response Headers:</div>
        <div class="sim-line dim">  X-SpendGuard-Run-Spent-Cents: ${result.headers["X-SpendGuard-Run-Spent-Cents"]}</div>
        <div class="sim-line dim">  X-SpendGuard-Run-Remaining-Cents: ${result.headers["X-SpendGuard-Run-Remaining-Cents"]}</div>
        <div class="sim-line dim">  X-SpendGuard-Run-Step: ${result.headers["X-SpendGuard-Run-Step"]}</div>
        <div class="sim-line dim">  X-SpendGuard-Run-Id: ${result.headers["X-SpendGuard-Run-Id"]}</div>
        <div class="sim-line dim">------------------------------------------------------------</div>
        <div class="sim-line" style="color: #fbbf24;">${result.body.error.message}</div>
      </div>
    `;

    showToast("Simulated protected call succeeded (blocked with 429)");
    state.onboarding = await api.getOnboardingState();
    // Refresh runs so the simulated run shows up in table
    loadRuns();
    renderOnboarding();
  } catch (err) {
    consoleEl.innerHTML = `<div class="sim-console"><div class="sim-line red">Simulation error: ${err.message}</div></div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ Re-run Simulated Call";
  }
}

// ============================================================================
// API Keys Tab Controller
// ============================================================================
async function loadKeys() {
  const container = document.getElementById("keys-table-body");
  const countBadge = document.getElementById("keys-count-badge");
  if (!container) return;

  container.innerHTML = `<tr><td colspan="6" class="state-container"><div class="loading-spinner"></div><div class="state-title">Loading API keys...</div></td></tr>`;

  try {
    const data = await api.listKeys();
    state.keys = data.keys;
    if (countBadge) countBadge.textContent = data.total;

    if (data.keys.length === 0) {
      container.innerHTML = `
        <tr>
          <td colspan="6">
            <div class="state-container">
              <div class="state-icon">🔑</div>
              <div class="state-title">No API keys created yet</div>
              <p class="state-desc">Create your first key to start authenticating protected agent requests.</p>
              <button id="empty-create-key-btn" class="btn btn-primary btn-sm">+ Create First Key</button>
            </div>
          </td>
        </tr>
      `;
      const btn = document.getElementById("empty-create-key-btn");
      if (btn) btn.addEventListener("click", openCreateKeyModal);
      return;
    }

    container.innerHTML = data.keys
      .map((k) => {
        const isRevoked = k.status === "revoked";
        const createdFormatted = new Date(k.created_at).toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          year: "numeric",
        });
        const lastUsedFormatted = k.last_used_at
          ? new Date(k.last_used_at).toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })
          : "Never";

        return `
          <tr class="${isRevoked ? "revoked-row" : ""}">
            <td data-label="Name"><strong>${escapeHtml(k.name)}</strong></td>
            <td data-label="Key">
              <span class="mono" style="font-size: 0.82rem;">${k.prefix}••••${k.last4}</span>
            </td>
            <td data-label="Created" class="mono" style="font-size: 0.8rem; color: var(--color-text-muted);">${createdFormatted}</td>
            <td data-label="Last Used" class="mono" style="font-size: 0.8rem; color: var(--color-text-muted);">${lastUsedFormatted}</td>
            <td data-label="Status">
              <span class="status-badge ${k.status}">${isRevoked ? "Revoked" : "Active"}</span>
            </td>
            <td data-label="Actions">
              ${
                isRevoked
                  ? `<span style="font-size: 0.75rem; color: var(--color-text-faint);">Disabled</span>`
                  : `<button class="btn btn-sm btn-danger revoke-key-btn" data-key-id="${k.key_id}" data-key-name="${escapeHtml(k.name)}">Revoke</button>`
              }
            </td>
          </tr>
        `;
      })
      .join("");

    // Bind revoke buttons
    document.querySelectorAll(".revoke-key-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.keyToRevoke = {
          id: btn.dataset.keyId,
          name: btn.dataset.keyName,
        };
        openRevokeConfirmModal();
      });
    });
  } catch (err) {
    container.innerHTML = `
      <tr>
        <td colspan="6">
          <div class="state-container">
            <div class="state-icon" style="color: var(--color-red);">⚠️</div>
            <div class="state-title">Failed to load API keys</div>
            <p class="state-desc">${escapeHtml(err.message)}</p>
            <button id="retry-keys-btn" class="btn btn-sm">Retry</button>
          </div>
        </td>
      </tr>
    `;
    const retryBtn = document.getElementById("retry-keys-btn");
    if (retryBtn) retryBtn.addEventListener("click", loadKeys);
  }
}

// Show-Once Key Creation Flow
function openCreateKeyModal() {
  const nameInput = document.getElementById("create-key-name-input");
  if (nameInput) nameInput.value = "";
  openModal("create-key-modal");
}

async function handleConfirmCreateKey(e) {
  e.preventDefault();
  const nameInput = document.getElementById("create-key-name-input");
  const name = (nameInput && nameInput.value.trim()) || "Production Agent Key";
  const submitBtn = document.getElementById("submit-create-key-btn");

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Creating...";
  }

  try {
    const result = await api.createKey({ name });
    closeModal("create-key-modal");

    // Open Show-Once Security Modal
    state.activeRawKey = result.raw_key;
    const rawDisplay = document.getElementById("show-once-raw-key");
    const checkbox = document.getElementById("saved-key-checkbox");
    const dismissBtn = document.getElementById("dismiss-show-once-btn");

    if (rawDisplay) rawDisplay.textContent = result.raw_key;
    if (checkbox) checkbox.checked = false;
    if (dismissBtn) dismissBtn.disabled = true;

    openModal("show-once-modal");

    // Refresh keys table & update onboarding
    loadKeys();
    state.onboarding = await api.getOnboardingState();
    renderOnboarding();
  } catch (err) {
    showToast(`Error creating key: ${err.message}`);
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Generate Key";
    }
  }
}

function openRevokeConfirmModal() {
  const nameDisplay = document.getElementById("revoke-key-name-display");
  if (nameDisplay && state.keyToRevoke) {
    nameDisplay.textContent = state.keyToRevoke.name;
  }
  openModal("revoke-confirm-modal");
}

async function handleConfirmRevokeKey() {
  if (!state.keyToRevoke) return;
  const btn = document.getElementById("confirm-revoke-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Revoking...";
  }

  try {
    await api.revokeKey(state.keyToRevoke.id);
    closeModal("revoke-confirm-modal");
    showToast(`Key '${state.keyToRevoke.name}' has been revoked`);
    loadKeys();
  } catch (err) {
    showToast(`Error revoking key: ${err.message}`);
  } finally {
    state.keyToRevoke = null;
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Yes, Revoke Key";
    }
  }
}

// ============================================================================
// Runs Tab Controller
// ============================================================================
async function loadRuns() {
  const container = document.getElementById("runs-table-body");
  const countBadge = document.getElementById("runs-count-badge");
  const pageDisplay = document.getElementById("runs-page-display");
  const prevBtn = document.getElementById("runs-prev-page-btn");
  const nextBtn = document.getElementById("runs-next-page-btn");
  if (!container) return;

  container.innerHTML = `<tr><td colspan="9" class="state-container"><div class="loading-spinner"></div><div class="state-title">Loading agent runs...</div></td></tr>`;

  try {
    const data = await api.listRuns({
      status: state.runsFilter,
      page: state.runsPage,
      limit: 10,
    });

    state.runs = data.runs;
    state.runsTotalPages = data.total_pages;

    if (countBadge) countBadge.textContent = data.total;
    if (pageDisplay) pageDisplay.textContent = `Page ${data.page} of ${data.total_pages}`;
    if (prevBtn) prevBtn.disabled = data.page <= 1;
    if (nextBtn) nextBtn.disabled = data.page >= data.total_pages;

    if (data.runs.length === 0) {
      container.innerHTML = `
        <tr>
          <td colspan="9">
            <div class="state-container">
              <div class="state-icon">🛡️</div>
              <div class="state-title">No runs matching filter '${state.runsFilter}'</div>
              <p class="state-desc">Make a protected call using your API key to monitor agent trajectories.</p>
            </div>
          </td>
        </tr>
      `;
      return;
    }

    container.innerHTML = data.runs
      .map((r) => {
        const startedFormatted = new Date(r.started_at).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        });

        const statusLabel = formatStatusLabel(r.status);

        return `
          <tr class="clickable" data-run-id="${r.run_id}">
            <td data-label="Run ID"><span class="mono" style="font-weight: 700; color: var(--color-accent);">${r.run_id}</span></td>
            <td data-label="Agent ID"><strong>${escapeHtml(r.agent_id || "default")}</strong></td>
            <td data-label="Status">
              <span class="status-badge ${r.status}">${statusLabel}</span>
            </td>
            <td data-label="Spent" class="mono">$${r.spent_usd.toFixed(2)}</td>
            <td data-label="Budget" class="mono">$${r.budget_usd.toFixed(2)}</td>
            <td data-label="Remaining" class="mono" style="color: ${r.remaining_usd === 0 ? "var(--color-red)" : "inherit"};">$${r.remaining_usd.toFixed(2)}</td>
            <td data-label="Steps" class="mono">${r.step_count} / ${r.max_steps}</td>
            <td data-label="Started" class="mono" style="font-size: 0.8rem; color: var(--color-text-muted);">${startedFormatted}</td>
            <td data-label="Action">
              ${
                r.status === "active"
                  ? `<button class="btn btn-sm btn-danger abort-run-btn" data-run-id="${r.run_id}" title="Kill-switch">Abort</button>`
                  : `<button class="btn btn-sm inspect-run-btn" data-run-id="${r.run_id}">Inspect</button>`
              }
            </td>
          </tr>
        `;
      })
      .join("");

    // Bind row clicks & abort buttons
    container.querySelectorAll("tr.clickable").forEach((row) => {
      row.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        const runId = row.dataset.runId;
        const run = state.runs.find((r) => r.run_id === runId);
        if (run) openDrawer(run);
      });
    });

    container.querySelectorAll(".inspect-run-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const run = state.runs.find((r) => r.run_id === btn.dataset.runId);
        if (run) openDrawer(run);
      });
    });

    container.querySelectorAll(".abort-run-btn").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const runId = btn.dataset.runId;
        btn.disabled = true;
        btn.textContent = "Aborting...";
        try {
          await api.abortRun(runId, "operator_manual_kill");
          showToast(`Run '${runId}' halted via operator kill-switch`);
          loadRuns();
        } catch (err) {
          showToast(`Error aborting run: ${err.message}`);
          btn.disabled = false;
          btn.textContent = "Abort";
        }
      });
    });
  } catch (err) {
    container.innerHTML = `
      <tr>
        <td colspan="9">
          <div class="state-container">
            <div class="state-icon" style="color: var(--color-red);">⚠️</div>
            <div class="state-title">Failed to load agent runs</div>
            <p class="state-desc">${escapeHtml(err.message)}</p>
            <button id="retry-runs-btn" class="btn btn-sm">Retry</button>
          </div>
        </td>
      </tr>
    `;
    const retryBtn = document.getElementById("retry-runs-btn");
    if (retryBtn) retryBtn.addEventListener("click", loadRuns);
  }
}

function renderRunDrawer(run) {
  const title = document.getElementById("drawer-run-title");
  const body = document.getElementById("drawer-run-body");
  const abortBtn = document.getElementById("drawer-abort-btn");

  if (title) title.textContent = `Run ${run.run_id}`;
  if (abortBtn) {
    abortBtn.style.display = run.status === "active" ? "inline-flex" : "none";
    abortBtn.onclick = async () => {
      abortBtn.disabled = true;
      abortBtn.textContent = "Aborting...";
      try {
        await api.abortRun(run.run_id);
        showToast(`Run '${run.run_id}' aborted`);
        closeDrawer();
        loadRuns();
      } catch (err) {
        showToast(`Failed to abort: ${err.message}`);
        abortBtn.disabled = false;
        abortBtn.textContent = "Emergency Abort";
      }
    };
  }

  if (body) {
    body.innerHTML = `
      <div style="margin-bottom: 1.25rem;">
        <span class="status-badge ${run.status}" style="font-size: 0.8rem; padding: 0.2rem 0.6rem;">
          ${formatStatusLabel(run.status)}
        </span>
      </div>

      ${
        run.tripwire_reason
          ? `
        <div style="background-color: var(--color-red-bg); border: 1px solid var(--color-red-border); border-radius: var(--radius-sm); padding: 0.75rem; margin-bottom: 1rem;">
          <div style="font-weight: 700; color: var(--color-red); font-size: 0.75rem; text-transform: uppercase;">Tripwire Reason</div>
          <div class="mono" style="font-size: 0.78rem; margin-top: 0.25rem;">${escapeHtml(run.tripwire_reason)}</div>
        </div>
      `
          : ""
      }

      <div style="margin-bottom: 1.25rem;">
        <div class="detail-row"><span class="detail-label">Agent ID</span><span class="detail-value mono">${escapeHtml(run.agent_id)}</span></div>
        <div class="detail-row"><span class="detail-label">Session ID</span><span class="detail-value mono">${escapeHtml(run.session_id || "None")}</span></div>
        <div class="detail-row"><span class="detail-label">Model Target</span><span class="detail-value mono">${escapeHtml(run.model || "gpt-4o")}</span></div>
        <div class="detail-row"><span class="detail-label">Spent USD</span><span class="detail-value mono">$${run.spent_usd.toFixed(2)}</span></div>
        <div class="detail-row"><span class="detail-label">Budget USD</span><span class="detail-value mono">$${run.budget_usd.toFixed(2)}</span></div>
        <div class="detail-row"><span class="detail-label">Remaining USD</span><span class="detail-value mono">$${run.remaining_usd.toFixed(2)}</span></div>
        <div class="detail-row"><span class="detail-label">Trajectory Step</span><span class="detail-value mono">${run.step_count} / ${run.max_steps}</span></div>
        <div class="detail-row"><span class="detail-label">Started At</span><span class="detail-value mono" style="font-size: 0.75rem;">${new Date(run.started_at).toISOString()}</span></div>
      </div>

      <div style="font-weight: 700; font-size: 0.85rem; margin-bottom: 0.5rem;">Pre-Flight Execution Log</div>
      <div style="background-color: var(--color-code-bg); border-radius: var(--radius-sm); padding: 0.75rem; font-family: var(--font-mono); font-size: 0.75rem; color: var(--color-code-text); line-height: 1.6;">
        <div>[0.00s] Pre-flight reserve: est_cost=12¢ &bull; Balance OK</div>
        <div>[0.45s] Upstream dispatch: tokens locked in Redis</div>
        <div>[1.20s] Step ${run.step_count}: status=${run.status}</div>
        ${run.tripwire_reason ? `<div style="color: #f87171;">[HALTED] ${escapeHtml(run.tripwire_reason)}</div>` : `<div style="color: #4ade80;">[TRUE-UP] Exact token cost settled</div>`}
      </div>
    `;
  }
}

function formatStatusLabel(status) {
  if (status === "active") return "● Active";
  if (status === "completed") return "✓ Completed";
  if (status === "budget_exceeded") return "▲ Budget Exceeded";
  if (status === "runaway_aborted") return "✕ Runaway Tripped";
  if (status === "aborted") return "■ Aborted";
  return status;
}

// ============================================================================
// Usage Tab Controller
// ============================================================================
async function loadUsage() {
  const gridContainer = document.getElementById("usage-grid-container");
  const chartContainer = document.getElementById("usage-chart-container");
  if (!gridContainer || !chartContainer) return;

  gridContainer.innerHTML = `<div class="metric-card"><div class="loading-spinner"></div><div style="text-align: center;">Loading usage...</div></div>`;

  try {
    const data = await api.getUsage({ period: state.usagePeriod });

    gridContainer.innerHTML = `
      <div class="metric-card">
        <div class="metric-label">Total Spend (${data.period === "month" ? "This Month" : "Last 7 Days"})</div>
        <div class="metric-value">$${data.total_spend_usd.toFixed(2)}</div>
        <div class="metric-sub">Upstream tokens settled</div>
      </div>

      <div class="metric-card">
        <div class="metric-label">Protected Requests</div>
        <div class="metric-value">${data.total_requests}</div>
        <div class="metric-sub">Pre-flight verified</div>
      </div>

      <div class="metric-card">
        <div class="metric-label">Runs by Status</div>
        <div class="mono" style="font-size: 0.88rem; margin-top: 0.25rem;">
          <div><strong style="color: var(--color-green);">${data.runs_by_status.completed}</strong> Completed</div>
          <div><strong style="color: var(--color-orange);">${data.runs_by_status.budget_exceeded}</strong> Budget Capped</div>
          <div><strong style="color: var(--color-red);">${data.runs_by_status.runaway_aborted}</strong> Loop Aborted</div>
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-label">Quota Remaining</div>
        <div class="metric-value" style="color: var(--color-accent);">$${data.quota_remaining_usd.toFixed(2)}</div>
        <div class="metric-sub">Workspace wallet balance</div>
      </div>
    `;

    renderUsageBarChart(data.daily_spend, chartContainer);
  } catch (err) {
    gridContainer.innerHTML = `
      <div class="metric-card" style="grid-column: 1 / -1;">
        <div style="color: var(--color-red); font-weight: 700;">Failed to load usage</div>
        <p style="font-size: 0.85rem; color: var(--color-text-muted);">${escapeHtml(err.message)}</p>
        <button id="retry-usage-btn" class="btn btn-sm" style="margin-top: 0.5rem;">Retry</button>
      </div>
    `;
    const retryBtn = document.getElementById("retry-usage-btn");
    if (retryBtn) retryBtn.addEventListener("click", loadUsage);
  }
}

function renderUsageBarChart(dailyItems, container) {
  const maxSpend = Math.max(0.5, ...dailyItems.map((d) => d.spend_usd));

  container.innerHTML = `
    <div class="chart-card">
      <div class="chart-header">
        <div>
          <h3 style="font-size: 1rem;">Daily Spend Breakdown</h3>
          <p style="font-size: 0.8rem; color: var(--color-text-muted);">True-up settled spend per day</p>
        </div>
        <div class="mono" style="font-size: 0.75rem; color: var(--color-text-muted);">
          Max: $${maxSpend.toFixed(2)}
        </div>
      </div>

      <div class="chart-bars-wrap">
        ${dailyItems
          .map((item) => {
            const heightPercent = Math.max(4, Math.round((item.spend_usd / maxSpend) * 100));
            return `
              <div class="chart-col">
                <div class="chart-tooltip">
                  $${item.spend_usd.toFixed(2)} &bull; ${item.requests} reqs
                </div>
                <div class="chart-bar" style="height: ${heightPercent}%;"></div>
              </div>
            `;
          })
          .join("")}
      </div>

      <div class="chart-x-labels">
        ${dailyItems.map((item) => `<div class="chart-x-label">${item.day}</div>`).join("")}
      </div>
    </div>
  `;
}

// ============================================================================
// Tab Switcher
// ============================================================================
function switchTab(tabName) {
  state.currentTab = tabName;

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });

  document.querySelectorAll(".tab-content").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tabName}`);
  });

  if (tabName === "keys") loadKeys();
  if (tabName === "runs") loadRuns();
  if (tabName === "usage") loadUsage();
}

// ============================================================================
// Utilities
// ============================================================================
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ============================================================================
// Main Initialization
// ============================================================================
document.addEventListener("DOMContentLoaded", () => {
  initTheme();

  // Theme toggle
  const themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) themeBtn.addEventListener("click", toggleTheme);

  // Setup Guide Link in Nav
  const setupGuideLink = document.getElementById("nav-setup-guide");
  if (setupGuideLink) {
    setupGuideLink.addEventListener("click", () => {
      const section = document.getElementById("onboarding-section");
      if (section) {
        section.style.display = "block";
        section.scrollIntoView({ behavior: "smooth" });
      }
    });
  }

  // Reset Mock Data Link
  const resetMockBtn = document.getElementById("demo-reset-btn");
  if (resetMockBtn) {
    resetMockBtn.addEventListener("click", (e) => {
      e.preventDefault();
      api.resetMockData();
    });
  }

  // Tab Navigation clicks
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      switchTab(btn.dataset.tab);
    });
  });

  // Create Key Button
  const createKeyBtn = document.getElementById("btn-open-create-key");
  if (createKeyBtn) createKeyBtn.addEventListener("click", openCreateKeyModal);

  // Create Key Form
  const createKeyForm = document.getElementById("create-key-form");
  if (createKeyForm) createKeyForm.addEventListener("submit", handleConfirmCreateKey);

  // Show-Once Modal Checkbox & Dismiss
  const savedCheckbox = document.getElementById("saved-key-checkbox");
  const dismissShowOnceBtn = document.getElementById("dismiss-show-once-btn");
  const copyRawKeyBtn = document.getElementById("copy-raw-key-btn");

  if (savedCheckbox && dismissShowOnceBtn) {
    savedCheckbox.addEventListener("change", (e) => {
      dismissShowOnceBtn.disabled = !e.target.checked;
    });
    dismissShowOnceBtn.addEventListener("click", () => {
      closeModal("show-once-modal");
      state.activeRawKey = null; // Clear from memory once saved & dismissed
      loadKeys();
    });
  }

  if (copyRawKeyBtn) {
    copyRawKeyBtn.addEventListener("click", () => {
      const val = document.getElementById("show-once-raw-key");
      if (val) copyText(val.textContent, "Secret key copied to clipboard");
    });
  }

  // Revoke Confirm Modal
  const confirmRevokeBtn = document.getElementById("confirm-revoke-btn");
  if (confirmRevokeBtn) confirmRevokeBtn.addEventListener("click", handleConfirmRevokeKey);

  const cancelRevokeBtn = document.getElementById("cancel-revoke-btn");
  if (cancelRevokeBtn) {
    cancelRevokeBtn.addEventListener("click", () => {
      closeModal("revoke-confirm-modal");
      state.keyToRevoke = null;
    });
  }

  // Runs Status Filter
  const statusFilter = document.getElementById("runs-status-filter");
  if (statusFilter) {
    statusFilter.addEventListener("change", (e) => {
      state.runsFilter = e.target.value;
      state.runsPage = 1;
      loadRuns();
    });
  }

  // Runs Pagination
  const prevPageBtn = document.getElementById("runs-prev-page-btn");
  const nextPageBtn = document.getElementById("runs-next-page-btn");
  if (prevPageBtn) {
    prevPageBtn.addEventListener("click", () => {
      if (state.runsPage > 1) {
        state.runsPage--;
        loadRuns();
      }
    });
  }
  if (nextPageBtn) {
    nextPageBtn.addEventListener("click", () => {
      if (state.runsPage < state.runsTotalPages) {
        state.runsPage++;
        loadRuns();
      }
    });
  }

  // Usage Period Select
  const usagePeriodSelect = document.getElementById("usage-period-select");
  if (usagePeriodSelect) {
    usagePeriodSelect.addEventListener("change", (e) => {
      state.usagePeriod = e.target.value;
      loadUsage();
    });
  }

  // Drawer Close
  const closeDrawerBtn = document.getElementById("close-drawer-btn");
  if (closeDrawerBtn) closeDrawerBtn.addEventListener("click", closeDrawer);

  // Close Modals on close-btn click
  document.querySelectorAll(".modal-close-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const modal = btn.closest(".modal-overlay");
      if (modal) modal.classList.remove("open");
    });
  });

  // Initial load
  initOnboarding();
  loadKeys();
});
