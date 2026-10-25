/* UTHO prototype login gate. Demo credentials are for demonstration only. */
(function () {
  "use strict";
  const employees = {"UTHO_184027":"A7K2M9Q4","UTHO_295163":"R4T8B1N6","UTHO_306841":"P5D2X7L9","UTHO_417592":"C3V8H1J6","UTHO_528406":"Z9F2K6R1","UTHO_639175":"M4Q7A2W8","UTHO_740283":"B6Y1E9T3","UTHO_851694":"N2G5U8C4","UTHO_962518":"H7P3D9S2","UTHO_173860":"L1R6V4A9","UTHO_284719":"T8C2M5Z1","UTHO_395027":"F4J9Q1B7","UTHO_406138":"W2N6K8D3","UTHO_517249":"E9S3P7H2","UTHO_628350":"Y5A1R8G4","UTHO_739461":"D6L2X9V3","UTHO_840572":"Q1B7T4M8","UTHO_951683":"G8W3C5N2","UTHO_162794":"K2Z6F1P9","UTHO_273805":"V9H4A2R7"};
  const storageKey = "utho_demo_password_overrides";
  const overrides = () => {
    try { return JSON.parse(localStorage.getItem(storageKey) || "{}"); }
    catch (_) { return {}; }
  };
  const currentPassword = (username) => overrides()[username] || employees[username];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
  let selected = "";
  let stage = "list";
  let reveal = false;
  let resolveReady;
  window.UTHO_AUTH_READY = new Promise(resolve => { resolveReady = resolve; });

  function frame() {
    let root = document.getElementById("utho-auth");
    if (!root) {
      root = document.createElement("section");
      root.id = "utho-auth";
      root.className = "utho-auth";
      root.setAttribute("aria-label", "UTHO employee login");
      document.body.appendChild(root);
    }
    return root;
  }
  function goIn() {
    sessionStorage.setItem("utho_demo_user", selected);
    frame().remove();
    document.body.classList.remove("utho-locked");
    resolveReady(selected);
  }
  function showList(message = "") {
    stage = "list";
    selected = "";
    frame().innerHTML = `
      <div class="utho-login-card utho-login-wide">
        <div class="utho-login-brand"><span class="utho-mark">U</span><div><strong>UTHO</strong><span>SmartCloud Optimizer</span></div></div>
        <p class="utho-eyebrow">SECURE WORKSPACE · DEMO ACCESS</p>
        <h1>Welcome back</h1>
        <div class="utho-test-note" role="note">
          <p>For test cases, refer this file for Usernames and Passwords. In the actual scenario, no such file would be existing.</p>
          <a class="utho-test-link" href="/credentials.txt" target="_blank" rel="noopener">Open Usernames &amp; Passwords file <span aria-hidden="true">↗</span></a>
        </div>
        <p class="utho-login-copy">Choose your employee username to continue.</p>
        ${message ? `<p class="utho-auth-message" role="alert">${esc(message)}</p>` : ""}
        <div class="utho-employee-grid">${Object.keys(employees).map(name =>
          `<button type="button" class="utho-employee" data-user="${name}"><span class="utho-avatar">${name.slice(-2)}</span><span><strong>${name}</strong><small>UTHO employee</small></span><span class="utho-chevron">›</span></button>`
        ).join("")}</div>
      </div>`;
  }
  function showPassword(message = "") {
    stage = "password";
    reveal = false;
    frame().innerHTML = `
      <div class="utho-login-card">
        <button class="utho-back" type="button" data-back>← All employees</button>
        <div class="utho-login-brand"><span class="utho-mark">U</span><div><strong>UTHO</strong><span>SmartCloud Optimizer</span></div></div>
        <p class="utho-eyebrow">EMPLOYEE SIGN-IN</p><h1>Enter your password</h1>
        <p class="utho-login-copy">Signing in as <strong>${esc(selected)}</strong></p>
        ${message ? `<p class="utho-auth-message" role="alert">${esc(message)}</p>` : ""}
        <form id="utho-password-form">
          <label for="utho-password">Password</label>
          <div class="utho-password-wrap"><input id="utho-password" name="password" type="password" autocomplete="current-password" required autofocus><button type="button" data-show>Show</button></div>
          <button class="utho-primary" type="submit">Sign in <span>→</span></button>
        </form>
        <p class="utho-auth-footnote">Your workspace is ready when you are.</p>
      </div>`;
    const input = document.getElementById("utho-password");
    if (input) input.focus();
  }
  function showChangeChoice() {
    stage = "choice";
    frame().innerHTML = `
      <div class="utho-login-card">
        <div class="utho-login-brand"><span class="utho-mark">U</span><div><strong>UTHO</strong><span>SmartCloud Optimizer</span></div></div>
        <p class="utho-eyebrow">SIGN-IN COMPLETE</p><h1>Change your password?</h1>
        <p class="utho-login-copy">Would you like to update the password for <strong>${esc(selected)}</strong> before entering the workspace?</p>
        <div class="utho-choice-actions"><button class="utho-secondary" type="button" data-change="no">No, continue</button><button class="utho-primary" type="button" data-change="yes">Yes, change password <span>→</span></button></div>
        <p class="utho-auth-footnote">You can continue without changing it.</p>
      </div>`;
  }
  function showChangeForm(message = "") {
    stage = "change";
    reveal = false;
    frame().innerHTML = `
      <div class="utho-login-card">
        <button class="utho-back" type="button" data-back-choice>← Back</button>
        <div class="utho-login-brand"><span class="utho-mark">U</span><div><strong>UTHO</strong><span>SmartCloud Optimizer</span></div></div>
        <p class="utho-eyebrow">ACCOUNT SECURITY</p><h1>Update password</h1>
        <p class="utho-login-copy">Use a password containing exactly 4 letters and 4 digits in any order.</p>
        ${message ? `<p class="utho-auth-message" role="alert">${esc(message)}</p>` : ""}
        <form id="utho-change-form">
          <label for="utho-original">Original password</label><input id="utho-original" name="original" type="password" autocomplete="current-password" required>
          <label for="utho-new">Changed password</label><input id="utho-new" name="changed" type="password" autocomplete="new-password" required>
          <label for="utho-confirm">Confirm changed password</label><input id="utho-confirm" name="confirm" type="password" autocomplete="new-password" required>
          <button class="utho-primary" type="submit">Save password and continue <span>→</span></button>
        </form>
      </div>`;
  }
  function validPassword(p) {
    return /^(?=(?:.*[A-Za-z]){4})(?=(?:.*\d){4})[A-Za-z\d]{8}$/.test(p)
      && (p.match(/[A-Za-z]/g) || []).length === 4
      && (p.match(/\d/g) || []).length === 4;
  }

  document.addEventListener("click", (event) => {
    const userButton = event.target.closest("[data-user]");
    if (userButton) { selected = userButton.dataset.user; showPassword(); return; }
    if (event.target.closest("[data-back]")) { showList(); return; }
    if (event.target.closest("[data-show]")) {
      const input = document.getElementById("utho-password");
      reveal = !reveal;
      input.type = reveal ? "text" : "password";
      event.target.closest("[data-show]").textContent = reveal ? "Hide" : "Show";
      input.focus();
      return;
    }
    const choice = event.target.closest("[data-change]");
    if (choice) { if (choice.dataset.change === "yes") showChangeForm(); else goIn(); return; }
    if (event.target.closest("[data-back-choice]")) { showChangeChoice(); }
  });
  document.addEventListener("submit", (event) => {
    if (event.target.id === "utho-password-form") {
      event.preventDefault();
      const entered = new FormData(event.target).get("password") || "";
      if (entered !== currentPassword(selected)) { showPassword("That password doesn't match. Please try again."); return; }
      showChangeChoice();
    }
    if (event.target.id === "utho-change-form") {
      event.preventDefault();
      const form = new FormData(event.target);
      const original = String(form.get("original") || "");
      const changed = String(form.get("changed") || "");
      const confirm = String(form.get("confirm") || "");
      if (original !== currentPassword(selected)) { showChangeForm("The original password is incorrect."); return; }
      if (!validPassword(changed)) { showChangeForm("Use exactly 4 letters and 4 digits, with no spaces or symbols."); return; }
      if (changed !== confirm) { showChangeForm("The changed password and confirmation do not match."); return; }
      if (changed === original) { showChangeForm("Choose a password different from the original."); return; }
      const all = overrides();
      all[selected] = changed;
      localStorage.setItem(storageKey, JSON.stringify(all));
      goIn();
    }
  });

  const activeUser = sessionStorage.getItem("utho_demo_user");
  if (activeUser && employees[activeUser]) {
    selected = activeUser;
    frame().remove();
    resolveReady(activeUser);
  } else {
    document.body.classList.add("utho-locked");
    showList();
  }
  window.UTHO_LOGOUT = function () {
    sessionStorage.removeItem("utho_demo_user");
    location.reload();
  };
})();
