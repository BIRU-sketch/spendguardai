/**
 * SpendGuard — Neo-Brutalist Auth Portal Interactions
 * Handles dual theme sync, tab switching, form validation,
 * asynchronous JSON sign-in / sign-up, and status orb integration.
 */

(function () {
  "use strict";

  // ------------------------------------------------------------------------
  // Theme Management (Synced with Landing Page)
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
    const btn = document.getElementById("auth-theme-toggle");
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

  applyTheme(getPreferredTheme());

  const themeBtn = document.getElementById("auth-theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "light";
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
    });
  }

  // ------------------------------------------------------------------------
  // Signature Status Orb State Machine
  // ------------------------------------------------------------------------
  const statusOrbCircle = document.getElementById("status-orb-circle");
  const statusOrbLabel = document.getElementById("status-orb-label");

  function setStatusOrbState(state, labelText) {
    if (!statusOrbCircle || !statusOrbLabel) return;
    statusOrbCircle.className = "status-orb-circle " + state;
    statusOrbLabel.textContent = labelText || (
      state === "spending" ? "STATUS // AUTHENTICATING" :
      state === "blocked" ? "STATUS // ACCESS DENIED" :
      "STATUS // GATEWAY READY"
    );
  }

  // ------------------------------------------------------------------------
  // Tab Switching
  // ------------------------------------------------------------------------
  const tabSignin = document.getElementById("tab-signin");
  const tabSignup = document.getElementById("tab-signup");
  const formSignin = document.getElementById("form-signin");
  const formSignup = document.getElementById("form-signup");
  const paneTitle = document.getElementById("auth-pane-title");
  const paneSubhead = document.getElementById("auth-pane-subhead");
  const alertBox = document.getElementById("auth-alert");

  function showAlert(msg, type = "error") {
    if (!alertBox) return;
    alertBox.textContent = msg;
    alertBox.className = `auth-alert-box visible ${type}`;
  }

  function clearAlert() {
    if (!alertBox) return;
    alertBox.textContent = "";
    alertBox.className = "auth-alert-box";
  }

  function switchTab(mode) {
    clearAlert();
    if (mode === "signin") {
      tabSignin.classList.add("active");
      tabSignup.classList.remove("active");
      formSignin.classList.remove("hidden");
      formSignup.classList.add("hidden");
      if (paneTitle) paneTitle.innerHTML = `Welcome back.<br><span class="hero-underline">Take control.</span>`;
      if (paneSubhead) paneSubhead.textContent = "Sign in to manage budget ceilings, API keys, and runaway loop safeguards.";
      setStatusOrbState("idle", "STATUS // SIGN IN");
    } else {
      tabSignup.classList.add("active");
      tabSignin.classList.remove("active");
      formSignup.classList.remove("hidden");
      formSignin.classList.add("hidden");
      if (paneTitle) paneTitle.innerHTML = `New workspace.<br><span class="hero-underline">Zero leakage.</span>`;
      if (paneSubhead) paneSubhead.textContent = "Create an account to deploy hard financial boundaries on autonomous AI agents.";
      setStatusOrbState("idle", "STATUS // CREATE ACCOUNT");
    }
  }

  if (tabSignin) tabSignin.addEventListener("click", () => switchTab("signin"));
  if (tabSignup) tabSignup.addEventListener("click", () => switchTab("signup"));

  // ------------------------------------------------------------------------
  // Password Visibility Toggles
  // ------------------------------------------------------------------------
  document.querySelectorAll(".password-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-target");
      const input = document.getElementById(targetId);
      if (!input) return;
      if (input.type === "password") {
        input.type = "text";
        btn.setAttribute("aria-label", "Hide password");
        btn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>`;
      } else {
        input.type = "password";
        btn.setAttribute("aria-label", "Show password");
        btn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>`;
      }
    });
  });

  // ------------------------------------------------------------------------
  // Handle Sign In (JSON POST /auth/signin)
  // ------------------------------------------------------------------------
  if (formSignin) {
    formSignin.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearAlert();

      const email = document.getElementById("signin-email").value.trim();
      const password = document.getElementById("signin-password").value;
      const btn = document.getElementById("btn-signin-submit");

      if (!email || !password) {
        showAlert("Email and password are required.");
        return;
      }

      btn.disabled = true;
      btn.textContent = "Verifying Credentials...";
      setStatusOrbState("spending", "STATUS // VERIFYING");

      try {
        const resp = await fetch("/auth/signin", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });

        const data = await resp.json().catch(() => ({}));

        if (!resp.ok) {
          throw new Error(data.error || "Authentication failed. Invalid email or password.");
        }

        setStatusOrbState("idle", "STATUS // AUTHORIZED");
        btn.textContent = "Redirecting...";
        window.location.href = data.redirect || "/dashboard";
      } catch (err) {
        setStatusOrbState("blocked", "STATUS // ACCESS DENIED");
        showAlert(err.message || "Sign in failed.", "error");
        btn.disabled = false;
        btn.textContent = "Authenticate & Enter →";
        setTimeout(() => {
          setStatusOrbState("idle", "STATUS // GATEWAY READY");
        }, 2200);
      }
    });
  }

  // ------------------------------------------------------------------------
  // Handle Sign Up (JSON POST /auth/signup)
  // ------------------------------------------------------------------------
  if (formSignup) {
    formSignup.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearAlert();

      const name = document.getElementById("signup-name").value.trim();
      const email = document.getElementById("signup-email").value.trim();
      const password = document.getElementById("signup-password").value;
      const confirm = document.getElementById("signup-confirm").value;
      const btn = document.getElementById("btn-signup-submit");

      if (!name || !email || !password) {
        showAlert("All fields are required.");
        return;
      }

      if (password.length < 8) {
        showAlert("Password must be at least 8 characters.");
        return;
      }

      if (password !== confirm) {
        showAlert("Passwords do not match.");
        return;
      }

      btn.disabled = true;
      btn.textContent = "Creating Workspace...";
      setStatusOrbState("spending", "STATUS // PROVISIONING");

      try {
        const resp = await fetch("/auth/signup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, email, password, confirm_password: confirm }),
        });

        const data = await resp.json().catch(() => ({}));

        if (!resp.ok) {
          throw new Error(data.error || "Account creation failed.");
        }

        if (resp.status === 202 || data.message) {
          setStatusOrbState("idle", "STATUS // CONFIRM EMAIL");
          showAlert(data.message || "Confirmation link sent to your email.", "success");
          btn.disabled = false;
          btn.textContent = "Create Free Workspace →";
          return;
        }

        setStatusOrbState("idle", "STATUS // WORKSPACE CREATED");
        btn.textContent = "Redirecting to Dashboard...";
        window.location.href = data.redirect || "/dashboard";
      } catch (err) {
        setStatusOrbState("blocked", "STATUS // SIGNUP FAILED");
        showAlert(err.message || "Failed to create account.", "error");
        btn.disabled = false;
        btn.textContent = "Create Free Workspace →";
        setTimeout(() => {
          setStatusOrbState("idle", "STATUS // GATEWAY READY");
        }, 2200);
      }
    });
  }

  // ------------------------------------------------------------------------
  // Check Query Parameters for Errors or Messages
  // ------------------------------------------------------------------------
  const urlParams = new URLSearchParams(window.location.search);
  const errorParam = urlParams.get("error");
  const msgParam = urlParams.get("message");
  if (errorParam) {
    showAlert(decodeURIComponent(errorParam), "error");
  } else if (msgParam) {
    showAlert(decodeURIComponent(msgParam), "success");
  }
})();
