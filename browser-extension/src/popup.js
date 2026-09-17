/**
 * Evanovar RAM Bridge popup.
 *
 * Mirrors the desktop application's account list: 22px circular avatar, the
 * account name, a `|` separator and the note in the same gold as the app, plus
 * a green corner dot for accounts that are currently running. Accounts can be
 * filtered by group using the chips above the list.
 */

import { BridgeClient, BridgeError, DEFAULT_PORT } from "./lib/bridge.js";
import {
  buildAddCommand,
  buildJoinUserCommand,
  buildLaunchCommand,
  buildMultiLaunchCommand,
  parseRobloxContext,
} from "./lib/roblox.js";

const STORAGE_KEYS = {
  port: "ram_port",
  token: "ram_token",
  account: "ram_account",
  avatars: "ram_avatars",
  checked: "ram_checked",
};

const CONNECT_HINT =
  "Make sure Evanovar RAM is running with Browser Extension enabled in Settings, Developer.";

const THUMBNAIL_API = "https://thumbnails.roblox.com/v1/users/avatar-headshot";

const client = new BridgeClient();

const state = {
  port: DEFAULT_PORT,
  token: "",
  selectedAccount: "",
  currentGroup: null,
  groups: [],
  accounts: [],
  runningNames: new Set(),
  runningIds: new Set(),
  avatars: new Map(),
  checked: new Set(),
  context: parseRobloxContext(""),
  busy: false,
};

const ui = {
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  setupView: document.getElementById("setup-view"),
  mainView: document.getElementById("main-view"),
  codeInput: document.getElementById("code-input"),
  linkButton: document.getElementById("link-button"),
  pageContext: document.getElementById("page-context"),
  groupBar: document.getElementById("group-bar"),
  accountList: document.getElementById("account-list"),
  launchPlaceButton: document.getElementById("launch-place"),
  launchJobButton: document.getElementById("launch-job"),
  launchSelectedButton: document.getElementById("launch-selected"),
  selectAllButton: document.getElementById("select-all"),
  selectNoneButton: document.getElementById("select-none"),
  joinInput: document.getElementById("join-input"),
  joinUserButton: document.getElementById("join-user"),
  saveAccountButton: document.getElementById("save-account"),
  log: document.getElementById("log"),
  portInput: document.getElementById("port-input"),
  refreshButton: document.getElementById("refresh-button"),
  unlinkButton: document.getElementById("unlink-button"),
};

// ---- status and views ----------------------------------------------------

function setStatus(kind, text) {
  ui.statusDot.className = `dot ${kind}`.trim();
  ui.statusText.textContent = text;
}

function notice(message, kind = "") {
  ui.log.className = `log ${kind}`.trim();
  ui.log.textContent = message;
}

function clearNotice() {
  ui.log.className = "log hidden";
  ui.log.textContent = "";
}

function renderSetupView() {
  ui.setupView.classList.remove("hidden");
  ui.mainView.classList.add("hidden");
  ui.unlinkButton.classList.add("hidden");
  updateControls();
}

function renderMainView() {
  ui.setupView.classList.add("hidden");
  ui.mainView.classList.remove("hidden");
  ui.unlinkButton.classList.remove("hidden");
  updateControls();
}

function updateControls() {
  const linked = Boolean(state.token);
  const hasSelection = Boolean(state.selectedAccount) && state.accounts.length > 0;
  const context = state.context || {};

  ui.linkButton.disabled = state.busy;
  ui.refreshButton.disabled = state.busy || !linked;
  ui.unlinkButton.disabled = state.busy;
  ui.launchPlaceButton.disabled = state.busy || !linked || !hasSelection || !context.placeId;
  ui.launchJobButton.disabled =
    state.busy || !linked || !hasSelection || !context.placeId || !context.jobId;
  ui.joinUserButton.disabled = state.busy || !linked || !hasSelection;
  ui.saveAccountButton.disabled = state.busy || !linked;

  const checkedCount = state.checked.size;
  ui.launchSelectedButton.textContent = checkedCount
    ? `Launch ${checkedCount} selected`
    : "Launch selected";
  ui.launchSelectedButton.disabled =
    state.busy || !linked || checkedCount === 0 || !context.placeId;
  ui.selectAllButton.disabled = state.busy || state.accounts.length === 0;
  ui.selectNoneButton.disabled = state.busy || checkedCount === 0;
}

function setBusy(value) {
  state.busy = Boolean(value);
  updateControls();
}

function updateSummary() {
  if (!state.token) {
    return;
  }
  const total = state.accounts.length;
  const running = state.accounts.filter(isRunning).length;
  const parts = [`${total} account${total === 1 ? "" : "s"}`];
  if (running > 0) {
    parts.push(`${running} running`);
  }
  setStatus("ok", parts.join(" - "));
}

function updateContextDisplay() {
  const context = state.context || {};
  ui.pageContext.textContent = context.label;
  ui.pageContext.classList.toggle("active", Boolean(context.placeId || context.userId));

  if (context.userId && !ui.joinInput.value.trim()) {
    ui.joinInput.value = context.userId;
  }

  updateControls();
}

// ---- account list --------------------------------------------------------

function isRunning(account) {
  if (state.runningNames.has(account.name)) {
    return true;
  }
  return Boolean(account.user_id) && state.runningIds.has(String(account.user_id));
}

function avatarUrlFor(account) {
  if (account.user_id) {
    const cached = state.avatars.get(String(account.user_id));
    if (cached) {
      return cached;
    }
  }
  return account.avatar_url || "";
}

function initialBadge(name) {
  const span = document.createElement("span");
  span.className = "initial";
  span.textContent = String(name || "?").slice(0, 1);
  return span;
}

function emptyRow(text) {
  const row = document.createElement("div");
  row.className = "empty";
  row.textContent = text;
  return row;
}

function createAccountRow(account) {
  const row = document.createElement("div");
  row.className = "account";
  row.dataset.name = account.name;
  row.tabIndex = 0;
  row.title = account.note ? `${account.name} | ${account.note}` : account.name;

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.className = "account-check";
  checkbox.checked = state.checked.has(account.name);
  checkbox.title = "Include in bulk launch";
  checkbox.addEventListener("click", (event) => event.stopPropagation());
  checkbox.addEventListener("change", () =>
    toggleChecked(account.name, checkbox.checked)
  );
  row.append(checkbox);

  if (account.cookie_valid === false) {
    row.classList.add("flagged");
    row.title += "\nCookie validation was rejected for this account.";
  }
  if (account.name === state.selectedAccount) {
    row.classList.add("selected");
  }

  const avatarWrap = document.createElement("span");
  avatarWrap.className = "account-avatar";

  const url = avatarUrlFor(account);
  if (url) {
    const image = document.createElement("img");
    image.src = url;
    image.alt = "";
    image.addEventListener("error", () => image.replaceWith(initialBadge(account.name)));
    avatarWrap.append(image);
  } else {
    avatarWrap.append(initialBadge(account.name));
  }

  if (isRunning(account)) {
    const running = document.createElement("span");
    running.className = "dot";
    running.title = "Running";
    avatarWrap.append(running);
  }

  const name = document.createElement("span");
  name.className = "account-name";
  name.textContent = account.name;
  row.append(avatarWrap, name);

  if (account.note) {
    const separator = document.createElement("span");
    separator.className = "note-sep";
    separator.textContent = "|";
    const note = document.createElement("span");
    note.className = "note";
    note.textContent = account.note;
    row.append(separator, note);
  }

  row.addEventListener("click", () => selectAccount(account.name));
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectAccount(account.name);
    }
  });
  return row;
}

function renderGroupBar() {
  ui.groupBar.replaceChildren();

  if (state.groups.length === 0) {
    ui.groupBar.classList.add("hidden");
    state.currentGroup = null;
    return;
  }

  ui.groupBar.classList.remove("hidden");
  ui.groupBar.append(groupChip("All", null));
  for (const group of state.groups) {
    ui.groupBar.append(groupChip(group, group));
  }
}

function groupChip(label, value) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "chip";
  chip.textContent = label;
  if (state.currentGroup === value) {
    chip.classList.add("active");
  }
  chip.addEventListener("click", () => {
    state.currentGroup = value;
    renderGroupBar();
    renderAccountList();
  });
  return chip;
}

function visibleAccounts() {
  if (!state.currentGroup) {
    return state.accounts;
  }
  return state.accounts.filter((account) => account.group === state.currentGroup);
}

function renderAccountList() {
  ui.accountList.replaceChildren();

  if (state.accounts.length === 0) {
    ui.accountList.append(emptyRow("No accounts, use 'Add Account' in Evanovar RAM."));
    updateControls();
    return;
  }

  const visible = visibleAccounts();
  if (visible.length === 0) {
    ui.accountList.append(emptyRow(`No accounts in ${state.currentGroup}.`));
    updateControls();
    return;
  }

  // Keep a usable selection when the group filter hides the selected account.
  if (!visible.some((account) => account.name === state.selectedAccount)) {
    state.selectedAccount = visible[0].name;
    saveSettings();
  }

  for (const account of visible) {
    ui.accountList.append(createAccountRow(account));
  }

  updateControls();
}

function checkedAccounts() {
  return state.accounts
    .filter((account) => state.checked.has(account.name))
    .map((account) => account.name);
}

function toggleChecked(name, isChecked) {
  if (isChecked) {
    state.checked.add(name);
  } else {
    state.checked.delete(name);
  }
  saveSettings();
  updateControls();
}

function selectAllInView() {
  for (const account of visibleAccounts()) {
    state.checked.add(account.name);
  }
  saveSettings();
  renderAccountList();
}

function clearSelection() {
  state.checked.clear();
  saveSettings();
  renderAccountList();
}

async function selectAccount(name) {
  state.selectedAccount = name;
  for (const row of ui.accountList.querySelectorAll(".account")) {
    row.classList.toggle("selected", row.dataset.name === name);
  }
  updateControls();
  await saveSettings();
}

function currentAccount() {
  return state.selectedAccount || "";
}

// ---- avatars -------------------------------------------------------------

/**
 * Fill in headshot URLs for accounts that do not have one yet. The desktop
 * application caches these itself; this only covers accounts imported before
 * that field existed. Results are cached in extension storage.
 */
async function resolveAvatars() {
  const missing = state.accounts.filter(
    (account) => account.user_id && !avatarUrlFor(account)
  );
  if (missing.length === 0) {
    return;
  }

  let cache = {};
  try {
    const stored = await chrome.storage.local.get(STORAGE_KEYS.avatars);
    cache = stored[STORAGE_KEYS.avatars] || {};
  } catch (error) {
    cache = {};
  }

  const pending = [];
  for (const account of missing) {
    const key = String(account.user_id);
    if (cache[key]) {
      state.avatars.set(key, cache[key]);
    } else {
      pending.push(account);
    }
  }

  if (pending.length === 0) {
    return;
  }

  try {
    const ids = pending.map((account) => account.user_id).join(",");
    const response = await fetch(
      `${THUMBNAIL_API}?userIds=${encodeURIComponent(ids)}` +
        "&size=100x100&format=Png&isCircular=true"
    );
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    let changed = false;
    for (const item of payload.data || []) {
      const url = String(item?.imageUrl || "");
      const key = String(item?.targetId || "");
      if (url && key) {
        cache[key] = url;
        state.avatars.set(key, url);
        changed = true;
      }
    }
    if (changed) {
      await chrome.storage.local.set({ [STORAGE_KEYS.avatars]: cache });
    }
  } catch (error) {
    // Avatars are cosmetic; a failure here must not break the popup.
  }
}

// ---- storage -------------------------------------------------------------

async function loadSettings() {
  const stored = await chrome.storage.local.get([
    STORAGE_KEYS.port,
    STORAGE_KEYS.token,
    STORAGE_KEYS.account,
  ]);

  const port = Number(stored[STORAGE_KEYS.port]);
  state.port = Number.isInteger(port) && port >= 1024 && port <= 65535 ? port : DEFAULT_PORT;
  state.token = String(stored[STORAGE_KEYS.token] || "");
  state.selectedAccount = String(stored[STORAGE_KEYS.account] || "");

  const checked = stored[STORAGE_KEYS.checked];
  state.checked = new Set(Array.isArray(checked) ? checked.map(String) : []);
}

async function saveSettings() {
  await chrome.storage.local.set({
    [STORAGE_KEYS.port]: state.port,
    [STORAGE_KEYS.token]: state.token,
    [STORAGE_KEYS.account]: state.selectedAccount,
    [STORAGE_KEYS.checked]: [...state.checked],
  });
}

async function clearLink() {
  state.token = "";
  state.accounts = [];
  state.groups = [];
  state.currentGroup = null;
  state.selectedAccount = "";
  state.runningNames = new Set();
  state.runningIds = new Set();
  state.checked = new Set();
  client.close("Unlinked.");
  await chrome.storage.local.remove(STORAGE_KEYS.token);
}

// ---- bridge --------------------------------------------------------------

async function connect() {
  if (client.connected) {
    return;
  }
  setStatus("warn", "Connecting...");
  await client.connect(state.port);
}

function isAuthError(error) {
  if (!(error instanceof BridgeError)) {
    return false;
  }
  const text = error.message.toLowerCase();
  return (
    text.includes("authentication failed") ||
    text.includes("password required") ||
    text.includes("auth format")
  );
}

function isConnectionError(error) {
  const text = String(error?.message || "").toLowerCase();
  return text.includes("could not connect") || text.includes("timed out connecting");
}

async function runCommand(command, options = {}) {
  return client.request(command, {
    token: options.authenticated === false ? "" : state.token,
    timeoutMs: options.timeoutMs,
  });
}

async function handleError(error) {
  if (isAuthError(error)) {
    await clearLink();
    renderSetupView();
    setStatus("warn", "Not linked");
    notice("The link was rejected. Pair the extension again.", "warn");
    return;
  }

  if (isConnectionError(error)) {
    setStatus("error", "Disconnected");
    notice(`${error.message} ${CONNECT_HINT}`, "error");
    return;
  }

  setStatus("error", "Failed");
  notice(error?.message || "Something went wrong.", "error");
}

async function performAction(action) {
  setBusy(true);
  clearNotice();
  try {
    await connect();
    const message = await action();
    if (message) {
      notice(message, "ok");
    }
    await refreshStatus();
  } catch (error) {
    await handleError(error);
  } finally {
    setBusy(false);
  }
}

// ---- data ----------------------------------------------------------------

function normalizeEntries(result) {
  // Newer builds return `entries`; older ones only return an `accounts` list.
  if (Array.isArray(result?.entries)) {
    return result.entries
      .map((entry) => ({
        name: String(entry?.name || ""),
        note: String(entry?.note || ""),
        group: String(entry?.group || ""),
        user_id: entry?.user_id || 0,
        avatar_url: String(entry?.avatar_url || ""),
        cookie_valid: entry?.cookie_valid,
      }))
      .filter((entry) => entry.name);
  }

  const names = Array.isArray(result?.accounts) ? result.accounts : [];
  return names.map((name) => ({
    name: String(name),
    note: "",
    group: "",
    user_id: 0,
    avatar_url: "",
    cookie_valid: undefined,
  }));
}

async function detectContext() {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    state.context = parseRobloxContext(tabs[0]?.url || "");
  } catch (error) {
    state.context = parseRobloxContext("");
  }
  updateContextDisplay();
}

async function refreshAccounts() {
  const result = await runCommand("AccountList");
  state.accounts = normalizeEntries(result);
  state.groups = Array.isArray(result?.groups) ? result.groups.map(String) : [];

  if (state.currentGroup && !state.groups.includes(state.currentGroup)) {
    state.currentGroup = null;
  }

  // Drop checked accounts that no longer exist.
  const known = new Set(state.accounts.map((account) => account.name));
  for (const name of [...state.checked]) {
    if (!known.has(name)) {
      state.checked.delete(name);
    }
  }
  if (!state.accounts.some((account) => account.name === state.selectedAccount)) {
    state.selectedAccount = state.accounts.length > 0 ? state.accounts[0].name : "";
  }

  await resolveAvatars();
  renderGroupBar();
  renderAccountList();

  if (state.accounts.length === 0) {
    notice("No accounts are saved in Evanovar RAM yet.", "warn");
  }
}

async function refreshStatus() {
  const result = await runCommand("GetStatus");
  const rows = Array.isArray(result) ? result : [];
  state.runningNames = new Set(
    rows.map((row) => row?.username).filter((name) => Boolean(name))
  );
  state.runningIds = new Set(
    rows.map((row) => row?.user_id).filter(Boolean).map((id) => String(id))
  );
  renderAccountList();
  updateSummary();
}

async function refreshAll() {
  await refreshAccounts();
  await refreshStatus();
}

// ---- actions -------------------------------------------------------------

async function linkExtension() {
  const code = ui.codeInput.value.trim();
  if (!code) {
    notice("Enter the pairing code shown in Evanovar RAM.", "warn");
    return;
  }

  setBusy(true);
  clearNotice();
  try {
    await connect();
    const result = await runCommand(`Pair ${code}`, { authenticated: false });
    const token = String(result?.token || "");
    if (!token) {
      throw new Error("The application did not return an access token.");
    }

    state.token = token;
    await saveSettings();
    ui.codeInput.value = "";

    renderMainView();
    setStatus("warn", "Loading accounts...");
    notice("Extension linked.", "ok");
    await refreshAll();
  } catch (error) {
    await handleError(error);
  } finally {
    setBusy(false);
  }
}

async function unlinkExtension() {
  setBusy(true);
  try {
    if (client.connected && state.token) {
      try {
        await runCommand("Unpair");
      } catch (error) {
        // The application may already have revoked the token.
      }
    }
  } finally {
    await clearLink();
    renderSetupView();
    setStatus("warn", "Not linked");
    notice("Extension unlinked.", "warn");
    setBusy(false);
  }
}

async function readSessionCookie() {
  const cookie = await chrome.cookies.get({
    url: "https://www.roblox.com/",
    name: ".ROBLOSECURITY",
  });
  if (!cookie || !cookie.value) {
    throw new Error("No Roblox session found. Log in to roblox.com in this browser first.");
  }
  return cookie.value;
}

// ---- events --------------------------------------------------------------

function bindEvents() {
  ui.linkButton.addEventListener("click", linkExtension);
  ui.unlinkButton.addEventListener("click", unlinkExtension);

  ui.codeInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      linkExtension();
    }
  });

  ui.joinInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      ui.joinUserButton.click();
    }
  });

  ui.refreshButton.addEventListener("click", () =>
    performAction(async () => {
      await refreshAccounts();
      await detectContext();
      return "Refreshed.";
    })
  );

  ui.launchPlaceButton.addEventListener("click", () =>
    performAction(async () => {
      const account = currentAccount();
      const command = buildLaunchCommand(account, state.context);
      const result = await runCommand(command);
      return `Launched ${result?.account || account} in place ${
        result?.place_id || state.context.placeId
      }.`;
    })
  );

  ui.launchSelectedButton.addEventListener("click", () =>
    performAction(async () => {
      const accounts = checkedAccounts();
      const command = buildMultiLaunchCommand(accounts, state.context);
      const result = await runCommand(command, {
        timeoutMs: 30000 + accounts.length * 20000,
      });
      const launched = Array.isArray(result?.accounts) ? result.accounts.length : accounts.length;
      const missing = Array.isArray(result?.missing) ? result.missing : [];
      const skipped = missing.length ? ` Skipped unsaved: ${missing.join(", ")}.` : "";
      return `Launched ${launched} account(s) in place ${
        result?.place_id || state.context.placeId
      }.${skipped}`;
    })
  );

  ui.selectAllButton.addEventListener("click", selectAllInView);
  ui.selectNoneButton.addEventListener("click", clearSelection);

  ui.launchJobButton.addEventListener("click", () =>
    performAction(async () => {
      const account = currentAccount();
      const command = buildLaunchCommand(account, state.context, { includeJobId: true });
      const result = await runCommand(command);
      return `Joined server ${result?.job_id || state.context.jobId}.`;
    })
  );

  ui.joinUserButton.addEventListener("click", () =>
    performAction(async () => {
      const target = ui.joinInput.value.trim();
      const command = buildJoinUserCommand(currentAccount(), target);
      const result = await runCommand(command, { timeoutMs: 30000 });
      return `Joined ${result?.target_user || target}.`;
    })
  );

  ui.saveAccountButton.addEventListener("click", () =>
    performAction(async () => {
      const cookie = await readSessionCookie();
      const result = await runCommand(buildAddCommand(cookie), { timeoutMs: 30000 });
      const imported = Array.isArray(result?.imported) ? result.imported : [];
      await refreshAccounts();
      return imported.length > 0
        ? `Saved ${imported.join(", ")}.`
        : "Account saved.";
    })
  );

  ui.portInput.addEventListener("change", async () => {
    const port = Number(ui.portInput.value);
    if (!Number.isInteger(port) || port < 1024 || port > 65535) {
      ui.portInput.value = String(state.port);
      notice("Enter a port between 1024 and 65535.", "warn");
      return;
    }
    if (port === state.port) {
      return;
    }

    state.port = port;
    client.close("Port changed.");
    await saveSettings();
    notice(`Using port ${port}.`, "ok");

    if (!state.token) {
      return;
    }
    try {
      await connect();
      await refreshAll();
    } catch (error) {
      await handleError(error);
    }
  });
}

// ---- startup -------------------------------------------------------------

async function init() {
  await loadSettings();
  ui.portInput.value = String(state.port);
  bindEvents();
  await detectContext();

  if (!state.token) {
    renderSetupView();
    setStatus("warn", "Not linked");
    notice("Link the extension with a pairing code from Evanovar RAM.");
    return;
  }

  try {
    await connect();
    await runCommand("Ping");
    renderMainView();
    await refreshAll();
  } catch (error) {
    await handleError(error);
  }
}

init();
