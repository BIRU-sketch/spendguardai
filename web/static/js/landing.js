/**
 * SpendGuard — Neo-Brutalist Landing Page Interactions
 * Handles theme toggling, live status orb state machine,
 * interactive hero simulation terminal, 5 bento mini-demos,
 * request-log console runner, SDK tabs, and copy commands.
 */

(function () {
  "use strict";

  // ------------------------------------------------------------------------
  // Theme Management (Light / Dark)
  // ------------------------------------------------------------------------
  const THEME_KEY = "spendguard-theme";

  function getPreferredTheme() {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
    updateThemeToggleIcon(theme);
  }

  function updateThemeToggleIcon(theme) {
    const btn = document.getElementById("theme-toggle");
    if (!btn) return;
    if (theme === "dark") {
      btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>`;
      btn.setAttribute("aria-label", "Switch to light theme");
      btn.setAttribute("title", "Switch to light theme");
    } else {
      btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>`;
      btn.setAttribute("aria-label", "Switch to dark theme");
      btn.setAttribute("title", "Switch to dark theme");
    }
  }

  const initialTheme = getPreferredTheme();
  applyTheme(initialTheme);

  const themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "light";
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
    });
  }

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    if (!localStorage.getItem(THEME_KEY)) {
      applyTheme(e.matches ? "dark" : "light");
    }
  });

  // ------------------------------------------------------------------------
  // Signature Status Orb State Machine
  // ------------------------------------------------------------------------
  const statusOrbEl = document.getElementById("status-orb");
  const statusOrbCircle = document.getElementById("status-orb-circle");
  const statusOrbLabel = document.getElementById("status-orb-label");

  function setStatusOrbState(state, labelText) {
    if (!statusOrbCircle || !statusOrbLabel) return;
    statusOrbCircle.className = "status-orb-circle " + state;
    statusOrbLabel.textContent = labelText || (
      state === "spending" ? "STATUS // ACTIVE SPEND" :
      state === "blocked" ? "STATUS // 429 BLOCKED" :
      "STATUS // SAFE (IDLE)"
    );
  }

  if (statusOrbEl) {
    statusOrbEl.addEventListener("click", () => {
      const demoSection = document.getElementById("hero-terminal-card");
      if (demoSection) {
        demoSection.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
  }

  // ------------------------------------------------------------------------
  // Typewriter Code Block in Hero (Syntax-Highlighted)
  // ------------------------------------------------------------------------
  const codeContentEl = document.getElementById("hero-code-content");
  const codeTokens = [
    { text: "from", cls: "syn-kw" },
    { text: " spendguard ", cls: "" },
    { text: "import", cls: "syn-kw" },
    { text: " SpendGuard\n\n", cls: "" },
    { text: "client", cls: "" },
    { text: " = SpendGuard(api_key=", cls: "" },
    { text: '"sg_live_..."', cls: "syn-str" },
    { text: ")\n\n", cls: "" },
    { text: "# Hard-bound agent trajectory in Redis\n", cls: "syn-cmt" },
    { text: "with", cls: "syn-kw" },
    { text: " client.run(budget=", cls: "" },
    { text: "0.50", cls: "syn-num" },
    { text: ", max_steps=", cls: "" },
    { text: "10", cls: "syn-num" },
    { text: ") ", cls: "" },
    { text: "as", cls: "syn-kw" },
    { text: " run:\n    response = run.chat(\n        model=", cls: "" },
    { text: '"gpt-4o"', cls: "syn-str" },
    { text: ',\n        messages=[{"role": ', cls: "" },
    { text: '"user"', cls: "syn-str" },
    { text: ', "content": ', cls: "" },
    { text: '"Analyze filings"', cls: "syn-str" },
    { text: "}]\n    )\n    ", cls: "" },
    { text: "print", cls: "syn-fn" },
    { text: '(\n        ', cls: "" },
    { text: 'f"Spent: ${run.spent_usd:.4f} | Status: {run.status}"', cls: "syn-str" },
    { text: "\n    )", cls: "" }
  ];

  let tokenIdx = 0;
  let charIdx = 0;
  let currentSpan = null;

  function renderAllTokensNow() {
    if (!codeContentEl) return;
    codeContentEl.innerHTML = "";
    codeTokens.forEach((t) => {
      if (t.cls) {
        const s = document.createElement("span");
        s.className = t.cls;
        s.textContent = t.text;
        codeContentEl.appendChild(s);
      } else {
        codeContentEl.appendChild(document.createTextNode(t.text));
      }
    });
  }

  function typeWriter() {
    if (!codeContentEl) return;
    if (tokenIdx >= codeTokens.length) return;

    const token = codeTokens[tokenIdx];
    if (charIdx === 0) {
      if (token.cls) {
        currentSpan = document.createElement("span");
        currentSpan.className = token.cls;
        codeContentEl.appendChild(currentSpan);
      } else {
        currentSpan = null;
      }
    }

    const char = token.text.charAt(charIdx);
    if (currentSpan) {
      currentSpan.textContent += char;
    } else {
      codeContentEl.appendChild(document.createTextNode(char));
    }

    charIdx++;
    if (charIdx >= token.text.length) {
      tokenIdx++;
      charIdx = 0;
      currentSpan = null;
    }

    const delay = char === "\n" ? 90 : (char === " " ? 15 : 20);
    setTimeout(typeWriter, delay);
  }

  // Check prefers-reduced-motion
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (prefersReducedMotion) {
    renderAllTokensNow();
  } else {
    setTimeout(typeWriter, 280);
  }

  // ------------------------------------------------------------------------
  // Hero Terminal Simulation
  // ------------------------------------------------------------------------
  const terminalLinesEl = document.getElementById("terminal-lines");
  const terminalRunBtn = document.getElementById("terminal-run-btn");
  const terminalResetBtn = document.getElementById("terminal-reset-btn");
  const terminalSpendStat = document.getElementById("terminal-spend-stat");
  const terminalProviderStat = document.getElementById("terminal-provider-stat");
  const heroTryDemoBtn = document.getElementById("hero-try-demo-btn");

  let simTimer = null;
  let isSimulating = false;

  const simulationScript = [
    {
      delay: 500,
      orbState: "spending",
      orbLabel: "STATUS // RESERVING $0.05",
      prompt: "[STEP 1]",
      text: "Trajectory initialized: run_id=run_9c82a, budget_cap=$0.50, max_steps=10",
      type: "info",
      spend: "$0.000",
      providerSpend: "$0.00"
    },
    {
      delay: 1100,
      orbState: "spending",
      orbLabel: "STATUS // SPEND $0.038",
      prompt: "[STEP 1]",
      text: "Lua Pre-flight reserved: $0.0500 | Provider response: 1,240 tokens. Actual cost: $0.0382. True-up refund: +$0.0118.",
      type: "success",
      spend: "$0.038",
      providerSpend: "$0.038"
    },
    {
      delay: 1400,
      orbState: "spending",
      orbLabel: "STATUS // TOOL CALL 1",
      prompt: "[STEP 2]",
      text: "Agent invoking tool: web_search(query='sec 10k financial statement 2026')",
      type: "info",
      spend: "$0.038",
      providerSpend: "$0.038"
    },
    {
      delay: 1200,
      orbState: "spending",
      orbLabel: "STATUS // SPEND $0.076",
      prompt: "[STEP 2]",
      text: "Call confirmed: 1,890 tokens. Actual cost: $0.0378. Trajectory spend: $0.0760 / $0.50.",
      type: "success",
      spend: "$0.076",
      providerSpend: "$0.076"
    },
    {
      delay: 1400,
      orbState: "spending",
      orbLabel: "STATUS // LOOP DETECTED",
      prompt: "[STEP 3]",
      text: "Agent repeats identical query 3x: 'sec 10k financial statement 2026'. Heuristic hash trip!",
      type: "warn",
      spend: "$0.076",
      providerSpend: "$0.076"
    },
    {
      delay: 1500,
      orbState: "blocked",
      orbLabel: "STATUS // 429 BLOCKED",
      prompt: "[ALERT]",
      text: "CIRCUIT BREAKER ENGAGED: runaway_loop_detected (consecutive_tool_repeat).",
      type: "error",
      spend: "$0.076",
      providerSpend: "$0.076"
    },
    {
      delay: 1000,
      orbState: "blocked",
      orbLabel: "STATUS // FAIL-CLOSED $0.00",
      prompt: "[SENTINEL]",
      text: "Upstream dispatch HALTED. Returned HTTP 429 budget_exceeded to caller.",
      type: "error",
      spend: "$0.076",
      providerSpend: "$0.076"
    },
    {
      delay: 900,
      orbState: "blocked",
      orbLabel: "STATUS // PROVIDER LEAK: $0.00",
      prompt: "[RESULT]",
      text: "Provider-side cost on blocked call: $0.00. Trajectory locked fail-closed.",
      type: "success",
      spend: "$0.076",
      providerSpend: "$0.076"
    }
  ];

  function runSimulation() {
    if (isSimulating) return;
    isSimulating = true;
    if (terminalRunBtn) terminalRunBtn.disabled = true;
    if (terminalLinesEl) terminalLinesEl.innerHTML = "";

    let stepIdx = 0;
    function nextStep() {
      if (stepIdx >= simulationScript.length) {
        isSimulating = false;
        if (terminalRunBtn) terminalRunBtn.disabled = false;
        setTimeout(() => {
          setStatusOrbState("idle", "STATUS // SAFE (IDLE)");
        }, 2500);
        return;
      }

      const item = simulationScript[stepIdx];
      setStatusOrbState(item.orbState, item.orbLabel);

      if (terminalLinesEl) {
        const line = document.createElement("div");
        line.className = "terminal-line";
        line.innerHTML = `
          <span class="terminal-prompt">${escapeHtml(item.prompt)}</span>
          <span class="terminal-text ${item.type}">${escapeHtml(item.text)}</span>
        `;
        terminalLinesEl.appendChild(line);
        terminalLinesEl.scrollTop = terminalLinesEl.scrollHeight;
      }

      if (terminalSpendStat) terminalSpendStat.textContent = item.spend;
      if (terminalProviderStat) terminalProviderStat.textContent = item.providerSpend;

      stepIdx++;
      simTimer = setTimeout(nextStep, item.delay);
    }

    nextStep();
  }

  function resetSimulation() {
    if (simTimer) clearTimeout(simTimer);
    isSimulating = false;
    if (terminalRunBtn) terminalRunBtn.disabled = false;
    setStatusOrbState("idle", "STATUS // SAFE (IDLE)");
    if (terminalSpendStat) terminalSpendStat.textContent = "$0.000";
    if (terminalProviderStat) terminalProviderStat.textContent = "$0.00";
    if (terminalLinesEl) {
      terminalLinesEl.innerHTML = `
        <div class="terminal-line">
          <span class="terminal-prompt">$</span>
          <span class="terminal-text">spendguard --listen --fail-closed --mode=production</span>
        </div>
        <div class="terminal-line">
          <span class="terminal-prompt">&gt;</span>
          <span class="terminal-text info">Redis Lua engine connected (0ms jitter). Awaiting agent trajectory...</span>
        </div>
      `;
    }
  }

  if (terminalRunBtn) terminalRunBtn.addEventListener("click", runSimulation);
  if (terminalResetBtn) terminalResetBtn.addEventListener("click", resetSimulation);
  if (heroTryDemoBtn) {
    heroTryDemoBtn.addEventListener("click", (e) => {
      e.preventDefault();
      const card = document.getElementById("hero-terminal-card");
      if (card) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        setTimeout(runSimulation, 600);
      }
    });
  }

  // ------------------------------------------------------------------------
  // Bento Mini-Demo 1: Hard Budget Slider & Runner
  // ------------------------------------------------------------------------
  const bentoSlider = document.getElementById("bento-budget-slider");
  const bentoBudgetVal = document.getElementById("bento-budget-val");
  const bentoSpendVal = document.getElementById("bento-spend-val");
  const bentoProgress = document.getElementById("bento-budget-progress");
  const bentoSimBtn = document.getElementById("bento-sim-step-btn");
  const bentoResetBtn = document.getElementById("bento-budget-reset-btn");
  const bentoStatusPill = document.getElementById("bento-budget-status");

  let bentoCurrentSpend = 0.0;

  function updateBudgetUI() {
    const budgetLimit = parseFloat(bentoSlider ? bentoSlider.value : "0.50");
    if (bentoBudgetVal) bentoBudgetVal.textContent = `$${budgetLimit.toFixed(2)}`;
    if (bentoSpendVal) bentoSpendVal.textContent = `$${bentoCurrentSpend.toFixed(3)}`;

    const pct = Math.min(100, (bentoCurrentSpend / budgetLimit) * 100);
    if (bentoProgress) {
      bentoProgress.style.width = `${pct}%`;
      if (pct >= 100) {
        bentoProgress.className = "progress-bar-fill blocked";
      } else if (pct > 75) {
        bentoProgress.className = "progress-bar-fill";
      } else {
        bentoProgress.className = "progress-bar-fill safe";
      }
    }

    if (bentoStatusPill) {
      if (bentoCurrentSpend >= budgetLimit) {
        bentoStatusPill.className = "pill pill-red";
        bentoStatusPill.textContent = "BLOCKED: 429 budget_exceeded";
      } else {
        bentoStatusPill.className = "pill pill-blue";
        bentoStatusPill.textContent = "STATUS: SAFE UNDER BUDGET";
      }
    }
  }

  if (bentoSlider) {
    bentoSlider.addEventListener("input", updateBudgetUI);
  }

  if (bentoSimBtn) {
    bentoSimBtn.addEventListener("click", () => {
      const budgetLimit = parseFloat(bentoSlider.value);
      if (bentoCurrentSpend >= budgetLimit) {
        alert("Trajectory halted! Hard budget ceiling reached. Upstream dispatch blocked.");
        return;
      }
      bentoCurrentSpend += 0.085;
      updateBudgetUI();
    });
  }

  if (bentoResetBtn) {
    bentoResetBtn.addEventListener("click", () => {
      bentoCurrentSpend = 0.0;
      updateBudgetUI();
    });
  }

  // ------------------------------------------------------------------------
  // Bento Mini-Demo 2: Atomic Reserve & True-Up Visualizer
  // ------------------------------------------------------------------------
  const reserveSimBtn = document.getElementById("bento-reserve-sim-btn");
  const reserveHoldVal = document.getElementById("reserve-hold-val");
  const reserveActualVal = document.getElementById("reserve-actual-val");
  const reserveRefundVal = document.getElementById("reserve-refund-val");
  const reserveBalVal = document.getElementById("reserve-bal-val");

  let baseBalance = 10.00;

  if (reserveSimBtn) {
    reserveSimBtn.addEventListener("click", () => {
      // Simulate estimated cost vs actual token output
      const estimatedHold = 0.050; // $0.05 max tokens hold
      const actualCost = +(0.012 + Math.random() * 0.018).toFixed(4); // e.g. $0.0215
      const refund = +(estimatedHold - actualCost).toFixed(4);

      if (reserveHoldVal) reserveHoldVal.textContent = `-$${estimatedHold.toFixed(4)}`;
      if (reserveActualVal) reserveActualVal.textContent = `$${actualCost.toFixed(4)}`;
      if (reserveRefundVal) reserveRefundVal.textContent = `+$${refund.toFixed(4)} (atomic true-up)`;

      baseBalance = +(baseBalance - actualCost).toFixed(4);
      if (reserveBalVal) reserveBalVal.textContent = `$${baseBalance.toFixed(2)}`;

      reserveRefundVal.parentElement.classList.add("refund");
    });
  }

  // ------------------------------------------------------------------------
  // Bento Mini-Demo 3: Runaway Loop Heuristics
  // ------------------------------------------------------------------------
  const loopBtns = document.querySelectorAll(".loop-opt-btn");
  const loopOutput = document.getElementById("loop-trip-output");

  const loopScenarios = {
    repetition: {
      text: "Repeated prompt hash [c8f1...]: Agent issued identical prompt 3 consecutive turns. Breaker tripped.",
      code: "ERR_RUNAWAY_REPEAT_PROMPT"
    },
    burst: {
      text: "High-frequency spinning: 16 API requests executed in 7.2 seconds (>15/10s burst window). Breaker tripped.",
      code: "ERR_RUNAWAY_HIGH_FREQUENCY"
    },
    steps: {
      text: "Trajectory limit reached: Agent reached max trajectory steps (10/10). Run closed.",
      code: "ERR_RUNAWAY_STEP_CAP"
    },
    tools: {
      text: "Identical tool call spiral: search(q='retry') called 4 times without context change. Trajectory aborted.",
      code: "ERR_RUNAWAY_TOOL_SPIRAL"
    }
  };

  loopBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      loopBtns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const key = btn.getAttribute("data-loop");
      const data = loopScenarios[key] || loopScenarios.repetition;
      if (loopOutput) {
        loopOutput.innerHTML = `<span style="color: var(--color-red); font-weight: 700;">[${data.code}]</span> <span style="margin-left: 6px;">${escapeHtml(data.text)}</span>`;
      }
    });
  });

  // ------------------------------------------------------------------------
  // Bento Mini-Demo 4: Operator Kill Switch
  // ------------------------------------------------------------------------
  const killswitchBtn = document.getElementById("operator-killswitch-btn");
  const killswitchStatus = document.getElementById("operator-killswitch-status");
  let isKilled = false;

  if (killswitchBtn) {
    killswitchBtn.addEventListener("click", () => {
      isKilled = !isKilled;
      if (isKilled) {
        killswitchBtn.textContent = "RESET TRAJECTORY";
        killswitchBtn.className = "chunky-interactive killswitch-btn engaged";
        if (killswitchStatus) {
          killswitchStatus.innerHTML = `<span style="color: var(--color-red); font-weight:800;">STATUS: ABORTED [operator_kill_switch]</span> — Redis abort flag set. Zero calls allowed.`;
        }
      } else {
        killswitchBtn.textContent = "ENGAGE KILL SWITCH";
        killswitchBtn.className = "chunky-interactive killswitch-btn";
        if (killswitchStatus) {
          killswitchStatus.innerHTML = `<span style="color: #16a34a; font-weight:800;">STATUS: RUNNING [Step 4/10]</span> — Heartbeat active, Lua reserve listening.`;
        }
      }
    });
  }

  // ------------------------------------------------------------------------
  // Bento Mini-Demo 5: Multi-Provider Routing
  // ------------------------------------------------------------------------
  const providerTabs = document.querySelectorAll(".provider-tab-btn");
  const providerCode = document.getElementById("provider-payload-preview");

  const providerSnippets = {
    openai: `// Outbound to OpenAI Gateway:
POST https://api.openai.com/v1/chat/completions
Model: gpt-4o
Reservation: $0.0500 pre-flight Lua locked
Adapter: Direct pass-through with usage reconciliation`,
    anthropic: `// Outbound to Anthropic Adapter:
POST https://api.anthropic.com/v1/messages
Model: claude-3-5-sonnet
Adapter: Translates OpenAI tools schema to Anthropic format
Reconciliation: Standardized USD cents usage`,
    gemini: `// Outbound to Gemini Adapter:
POST https://generativelanguage.googleapis.com/...:generateContent
Model: gemini-2.0-flash
Adapter: Translates system/user turns to contents/parts
Reconciliation: Atomic reserve & exact token cost math`,
    groq: `// Outbound to Groq Compatible:
POST https://api.groq.com/openai/v1/chat/completions
Model: llama-3.3-70b-versatile
Latency: Sub-millisecond budget check overhead (<2ms)`
  };

  providerTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      providerTabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const p = tab.getAttribute("data-provider");
      if (providerCode && providerSnippets[p]) {
        providerCode.textContent = providerSnippets[p];
      }
    });
  });

  // ------------------------------------------------------------------------
  // Request-Log Console Runner
  // ------------------------------------------------------------------------
  const consoleTableBody = document.getElementById("console-table-body");
  const consoleRunBtn = document.getElementById("console-run-btn");
  const consoleResetBtn = document.getElementById("console-reset-btn");
  const totalCostStat = document.getElementById("console-total-cost");
  const totalSavedStat = document.getElementById("console-total-saved");

  let consoleStep = 1;
  let cumCost = 0.0;
  let cumRefund = 0.0;

  const mockSteps = [
    { model: "gpt-4o", reserved: 0.050, cost: 0.0214, status: "200 OK" },
    { model: "claude-3-5-sonnet", reserved: 0.060, cost: 0.0381, status: "200 OK" },
    { model: "gpt-4o-mini", reserved: 0.020, cost: 0.0042, status: "200 OK" },
    { model: "gemini-2.0-flash", reserved: 0.015, cost: 0.0028, status: "200 OK" },
    { model: "gpt-4o", reserved: 0.050, cost: 0.000, status: "429 budget_exceeded (CAPPED)", blocked: true }
  ];

  function runNextConsoleStep() {
    if (consoleStep > mockSteps.length) {
      alert("All simulated steps executed. Trajectory halted fail-closed.");
      return;
    }

    const item = mockSteps[consoleStep - 1];
    const refund = item.blocked ? 0.00 : item.reserved - item.cost;
    cumCost += item.cost;
    cumRefund += refund;

    const row = document.createElement("tr");
    if (item.blocked) row.className = "blocked-row";

    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    row.innerHTML = `
      <td>#${consoleStep}</td>
      <td>${now}</td>
      <td>${escapeHtml(item.model)}</td>
      <td>$${item.reserved.toFixed(4)}</td>
      <td>$${item.cost.toFixed(4)}</td>
      <td>${item.blocked ? "$0.0000" : "+$" + refund.toFixed(4)}</td>
      <td><span class="pill ${item.blocked ? 'pill-red' : 'pill-blue'}">${escapeHtml(item.status)}</span></td>
    `;

    if (consoleTableBody) consoleTableBody.appendChild(row);
    if (totalCostStat) totalCostStat.textContent = `$${cumCost.toFixed(4)}`;
    if (totalSavedStat) totalSavedStat.textContent = `$${cumRefund.toFixed(4)}`;

    consoleStep++;
  }

  function resetConsole() {
    consoleStep = 1;
    cumCost = 0.0;
    cumRefund = 0.0;
    if (consoleTableBody) consoleTableBody.innerHTML = "";
    if (totalCostStat) totalCostStat.textContent = "$0.0000";
    if (totalSavedStat) totalSavedStat.textContent = "$0.0000";
    runNextConsoleStep();
  }

  if (consoleRunBtn) consoleRunBtn.addEventListener("click", runNextConsoleStep);
  if (consoleResetBtn) consoleResetBtn.addEventListener("click", resetConsole);

  // Initialize first row
  runNextConsoleStep();

  // ------------------------------------------------------------------------
  // SDK Code Tabs
  // ------------------------------------------------------------------------
  const sdkTabs = document.querySelectorAll(".sdk-tab-btn");
  const sdkCode = document.getElementById("sdk-code-display");

  const sdkSnippets = {
    python: `# Python SDK (sync & async supported)
from spendguard import SpendGuard

client = SpendGuard(api_key="sg_live_...")

# Budget is bounded per trajectory; auto-refunds on exit
with client.run(budget=0.50, max_steps=10) as run:
    response = run.chat(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Analyze filings"}]
    )
    print(f"Spent: \${run.spent_usd:.4f} | Status: {run.status}")`,

    typescript: `// TypeScript / Node SDK (ESM & CJS)
import { SpendGuard } from "spendguard";

const client = new SpendGuard({ apiKey: process.env.SPENDGUARD_API_KEY });

// Initialize bounded trajectory
const run = await client.run({ budget: 0.50, maxSteps: 10 });

const res = await run.chat({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Analyze filings" }],
});

console.log(\`Spent: $\${run.spentUsd} | Remaining: $\${run.remainingUsd}\`);`,

    openai: `// Zero SDK changes: drop-in OpenAI baseURL override
import OpenAI from "openai";

const openai = new OpenAI({
  baseURL: "https://gateway.spendguard.io/v1", // or http://localhost:8000/v1
  apiKey: "sg_live_...",
  defaultHeaders: {
    "X-SpendGuard-Run-Budget-Cents": "50", // $0.50 hard cap
    "X-SpendGuard-Max-Steps": "10",
  },
});

const completion = await openai.chat.completions.create({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Analyze filings" }],
});`
  };

  sdkTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      sdkTabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const lang = tab.getAttribute("data-lang");
      if (sdkCode && sdkSnippets[lang]) {
        sdkCode.textContent = sdkSnippets[lang];
      }
    });
  });

  // ------------------------------------------------------------------------
  // Install Command Copy & Tabs
  // ------------------------------------------------------------------------
  const installTabs = document.querySelectorAll(".install-tab-btn");
  const installCmdEl = document.getElementById("install-command-text");
  const copyBtn = document.getElementById("copy-install-btn");

  const installCommands = {
    pip: "pip install spendguard",
    npm: "npm install spendguard"
  };

  installTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      installTabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const pkg = tab.getAttribute("data-pkg");
      if (installCmdEl && installCommands[pkg]) {
        installCmdEl.textContent = installCommands[pkg];
      }
    });
  });

  if (copyBtn && installCmdEl) {
    copyBtn.addEventListener("click", () => {
      const text = installCmdEl.textContent.trim();
      navigator.clipboard.writeText(text).then(() => {
        const orig = copyBtn.textContent;
        copyBtn.textContent = "COPIED!";
        setTimeout(() => {
          copyBtn.textContent = orig;
        }, 1800);
      }).catch(() => {
        alert("Copied to clipboard: " + text);
      });
    });
  }

  // ------------------------------------------------------------------------
  // FAQ Accordion
  // ------------------------------------------------------------------------
  const faqCards = document.querySelectorAll(".faq-card");
  faqCards.forEach((card) => {
    const btn = card.querySelector(".faq-question");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const isOpen = card.classList.contains("open");
      faqCards.forEach((c) => c.classList.remove("open"));
      if (!isOpen) card.classList.add("open");
    });
  });

  // ------------------------------------------------------------------------
  // Scroll Entrance Observer (16px rise, once)
  // ------------------------------------------------------------------------
  if ("IntersectionObserver" in window && !prefersReducedMotion) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("in-view");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });

    document.querySelectorAll(".rise-card").forEach((el) => {
      observer.observe(el);
    });
  } else {
    document.querySelectorAll(".rise-card").forEach((el) => {
      el.classList.add("in-view");
    });
  }

  // Helper utility
  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
})();
