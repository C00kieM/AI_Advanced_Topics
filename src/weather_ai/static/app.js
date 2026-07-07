const state = {
  history: [],
  historyIndex: -1,
  jobLogCounts: new Map(),
  polling: new Map(),
  timings: [],
  pendingCommand: null,
  refreshing: false,
};

const labels = {
  temperature: "Temperatur",
  precipitation: "Niederschlag",
  wind_speed: "Wind",
};

const API_TIMEOUT_MS = 120000;
const CHAT_TIMEOUT_MS = 180000;
const STATUS_TIMEOUT_MS = 90000;
const LIVE_STATUS_TIMEOUT_MS = 180000;
const JOB_TIMEOUT_MS = 45000;
const FILE_TIMEOUT_MS = 30000;

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", () => {
  bindUi();
  setView(new URLSearchParams(window.location.search).get("view"));
  bootTerminal();
  refreshAll(false);
  setInterval(() => refreshAll(false, { quiet: true }), 15000);
  setInterval(() => refreshJobs({ quiet: true }), 3000);
});

function bindUi() {
  $("terminal-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitInput($("terminal-input"), "ops");
  });

  $("chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitInput($("chat-input"), "chat");
  });

  bindHistory($("terminal-input"));
  bindHistory($("chat-input"));

  document.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => runTerminalInput(button.dataset.command, "ops"));
  });

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });

  document.querySelectorAll("[data-open-file]").forEach((button) => {
    button.addEventListener("click", () => openDataFile(button.dataset.openFile, button));
  });

  $("refresh-status").addEventListener("click", () => refreshAll(true));
}

function submitInput(input, source) {
  const value = input.value.trim();
  if (!value) return;
  input.value = "";
  runTerminalInput(value, source);
}

function bindHistory(input) {
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowUp") {
      event.preventDefault();
      recallHistory(-1);
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      recallHistory(1);
    }
  });
}

function bootTerminal(source = "all") {
  if (source === "all" || source === "ops") {
    appendTerminalLine("system", "Weather Ops Admin View bereit.");
    appendTerminalLine(
      "system",
      "Nutze /info fuer Befehle. Freitext und Slash-Kommandos laufen ueber den lokalen KI-Chat."
    );
  }
  if (source === "all" || source === "chat") {
    appendChatLine("system", "Hallo, ich bin dein Wetterassistent. Frag mich nach Prognosen, vergangenen Tagen oder einer bestimmten Station.");
  }
}

async function openDataFile(target, button) {
  if (!target) return;
  const oldDisabled = button?.disabled;
  if (button) button.disabled = true;
  setTerminalState("oeffnet");
  try {
    const payload = await api("/files/open", {
      method: "POST",
      body: JSON.stringify({ target }),
      timeout: FILE_TIMEOUT_MS,
    });
    appendTerminalLine("success", `${payload.label || "Datei"} wurde geoeffnet.`);
  } catch (error) {
    appendTerminalLine("error", `Datei konnte nicht geoeffnet werden: ${error.message || error}`);
  } finally {
    if (button) button.disabled = Boolean(oldDisabled);
    setTerminalState("bereit");
  }
}

async function runTerminalInput(value, source = "ops") {
  remember(value);
  appendToSource(source, "user", `> ${value}`);
  const commandFromConfirmation = resolvePendingConfirmation(value, source);
  if (commandFromConfirmation === false) return;
  const submittedValue = commandFromConfirmation || value;
  setTerminalState("arbeitet");
  const startedAt = performance.now();
  try {
    const response = await askChat(submittedValue);
    await handleChatResponse(response, source);
  } catch (error) {
    appendToSource(source, "error", error.message || String(error));
  } finally {
    const durationMs = performance.now() - startedAt;
    const labelText = submittedValue.startsWith("/") ? submittedValue : "Chat";
    recordTiming(labelText, durationMs, "chat");
    setTerminalState("bereit");
  }
}

async function askChat(question) {
  return api("/chat", {
    method: "POST",
    body: JSON.stringify({ question }),
    timeout: CHAT_TIMEOUT_MS,
  });
}

async function handleChatResponse(response, source = "ops") {
  if (response.type === "clear") {
    clearOutputs(source);
    bootTerminal(source);
    await refreshAll(false, { quiet: true, force: true });
    return;
  }
  if (response.answer) {
    appendToSource(source, response.type === "confirmation" ? "warning" : "system", response.answer);
  }
  if (response.type === "confirmation") {
    state.pendingCommand = response.command || null;
    return;
  }
  state.pendingCommand = null;
  if (response.type === "status" && response.status) {
    renderStatus(response.status);
    await refreshJobs({ quiet: true });
    return;
  }
  if (response.type === "comparison" && response.comparison) {
    renderComparison(response.comparison);
    await refreshAll(false, { quiet: true, force: true });
    return;
  }
  if (response.type === "job" && response.job) {
    announceJob(response.job, source);
    pollJob(response.job.id);
    await refreshAll(false, { quiet: true, force: true });
  }
}

async function refreshAll(live = false, options = {}) {
  if (state.refreshing && !options.force) return;
  state.refreshing = true;
  try {
    await Promise.allSettled([refreshStatus(live, options), refreshJobs(options)]);
  } finally {
    state.refreshing = false;
  }
}

async function refreshStatus(live, options = {}) {
  if (!options.quiet) setTerminalState(live ? "live" : "status");
  try {
    const status = await api(`/status?live=${live ? "true" : "false"}`, { timeout: live ? LIVE_STATUS_TIMEOUT_MS : STATUS_TIMEOUT_MS });
    renderStatus(status);
    if (!options.quiet && status.live?.warnings?.length) {
      appendTerminalLine("warning", `Status mit ${status.live.warnings.length} Warnung(en) aktualisiert.`);
    }
  } catch (error) {
    if (!options.quiet) appendTerminalLine("error", `Status konnte nicht geladen werden: ${error.message || error}`);
  } finally {
    if (!options.quiet) setTerminalState("bereit");
  }
}

async function refreshJobs(options = {}) {
  try {
    const payload = await api("/jobs", { timeout: JOB_TIMEOUT_MS });
    renderJobs(payload.jobs || []);
  } catch (error) {
    if (!options.quiet) appendTerminalLine("error", `Jobs konnten nicht geladen werden: ${error.message || error}`);
  }
}

function announceJob(job, source = "ops") {
  appendTerminalLine("system", `Job ${job.id} gestartet: ${job.name}`);
  if (source === "chat") {
    appendChatLine("system", `Ich habe "${job.name}" gestartet. Die technischen Logs bleiben in der Admin View.`);
  }
  renderJobs([job]);
}

function pollJob(jobId) {
  if (state.polling.has(jobId)) return;
  const timer = setInterval(async () => {
    try {
      const job = await api(`/jobs/${jobId}`, { timeout: JOB_TIMEOUT_MS });
      renderJobLogs(job);
      await refreshJobs({ quiet: true });
      if (["succeeded", "failed"].includes(job.status)) {
        clearInterval(timer);
        state.polling.delete(jobId);
        if (job.status === "succeeded") {
          if (job.result?.warning) {
            appendTerminalLine("warning", `Job ${job.id} abgeschlossen in ${formatSeconds(job.duration_seconds)} mit Warnung: ${job.result.warning}`);
          } else {
            appendTerminalLine("success", `Job ${job.id} abgeschlossen in ${formatSeconds(job.duration_seconds)}.`);
          }
        } else {
          appendTerminalLine("error", `Job ${job.id} fehlgeschlagen nach ${formatSeconds(job.duration_seconds)}: ${job.error}`);
        }
        recordTiming(job.name, (job.duration_seconds || 0) * 1000, "job");
        await refreshAll(false, { quiet: true });
      }
    } catch (error) {
      appendTerminalLine("error", `Job ${jobId} konnte nicht gelesen werden: ${error.message || error}`);
      clearInterval(timer);
      state.polling.delete(jobId);
    }
  }, 1200);
  state.polling.set(jobId, timer);
}

function renderJobLogs(job) {
  const previous = state.jobLogCounts.get(job.id) || 0;
  const nextLogs = (job.logs || []).slice(previous);
  nextLogs.forEach((line) => appendTerminalLine(job.status === "failed" ? "error" : "system", `${job.id} ${line}`));
  state.jobLogCounts.set(job.id, (job.logs || []).length);
}

function resolvePendingConfirmation(value, source = "ops") {
  if (!state.pendingCommand) return null;
  const normalized = normalize(value);
  if (isAffirmative(normalized)) {
    const command = state.pendingCommand;
    state.pendingCommand = null;
    appendToSource(source, "system", `Bestaetigt. Ich starte ${command}.`);
    return command;
  }
  if (isNegative(normalized)) {
    state.pendingCommand = null;
    appendToSource(source, "system", "Alles klar, ich starte die Aktion nicht.");
    return false;
  }
  state.pendingCommand = null;
  appendToSource(source, "system", "Offene Bestaetigung verworfen.");
  return null;
}

function isAffirmative(value) {
  return ["ja", "j", "yes", "ok", "okay", "start", "starte", "ausfuehren", "mach"].includes(value);
}

function isNegative(value) {
  return ["nein", "n", "no", "stop", "abbrechen", "cancel"].includes(value);
}

function setView(view) {
  const target = view === "chat" ? "chat" : "ops";
  document.body.classList.toggle("view-chat", target === "chat");
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === target);
  });
  const input = target === "chat" ? $("chat-input") : $("terminal-input");
  if (input) input.focus({ preventScroll: true });
}

function renderStatus(payload) {
  const live = payload.live || {};
  const config = payload.config || {};
  const localCache = payload.local_cache || {};
  const strunde = payload.strunde_cache || {};
  const dwd = payload.dwd_data || {};
  const comparison = payload.comparison || {};
  const models = payload.models || {};
  const liveChecked = live.checked !== false;
  const influxTone = liveChecked ? (live.influx_ok ? "good" : "bad") : "unknown";
  const dwdTone = liveChecked ? (live.dwd_ok ? "good" : "bad") : "unknown";
  const cacheTone = !localCache.exists ? "bad" : localCache.stale ? "warn" : "good";
  const forecastCount = Number(dwd.forecast_rows || 0);
  const pairCount = Number(comparison.pairs || 0);
  const dwdFrom = dwd.observation_min_time || dwd.min_time || dwd.min_valid_at;
  const dwdTo = dwd.observation_max_time || dwd.max_time || dwd.max_valid_at;

  setChip("chip-influx", influxTone, "InfluxDB");
  setChip("chip-dwd", dwdTone, "DWD");
  setDot("influx-dot", influxTone);
  setDot("dwd-dot", dwdTone);

  setSignal("signal-influx", influxTone, "influx-state", liveChecked ? (live.influx_ok ? "OK" : "Fehler") : "nicht geprueft");
  setSignal("signal-dwd", forecastCount > 0 ? "good" : dwd.exists ? "warn" : "bad", "dwd-state", forecastCount > 0 ? `${number(forecastCount)} Forecasts` : dwd.exists ? "nur Historie" : "fehlt");
  setSignal("signal-cache", cacheTone, "local-state", !localCache.exists ? "fehlt" : localCache.stale ? "veraltet" : "aktuell");
  setSignal("signal-models", pairCount > 0 ? "good" : "warn", "compare-pairs", `${number(pairCount)} Paare`);

  setText("status-generated", `Stand ${formatDate(payload.generated_at)}`);
  setText("influx-health", liveChecked ? (live.influx_ok ? "OK" : "Fehler") : "nicht geprueft");
  setText("dwd-health", liveChecked ? (live.dwd_ok ? "OK" : "Fehler") : "nicht geprueft");
  setText("influx-url", config.influx_url || "-");
  setText("influx-org", config.influx_org || "-");
  setText("influx-bucket", config.influx_bucket || "-");
  setText("local-measurement", config.local_measurement || "-");
  setText("local-measurement-detail", config.local_measurement || "-");
  setText("influx-token", config.influx_token || "-");
  setText("mosmix-station", config.mosmix_station_id || config.dwd_station_id || "-");
  setText("mosmix-product", config.mosmix_product || "-");
  setText("forecast-rows", number(dwd.forecast_rows));
  setText("forecast-valid", dwdTo ? `Daten bis ${formatDate(dwdTo)}` : "keine DWD-Daten");
  setText("dwd-from", formatDate(dwdFrom));
  setText("dwd-to", formatDate(dwdTo));
  setText("forecast-from", formatDate(dwd.min_valid_at));
  setText("forecast-to", formatDate(dwd.max_valid_at));
  setText("dwd-size", bytes(dwd.size_bytes));

  setText("cache-rows", number(localCache.rows));
  setText("cache-selected-rows", number(localCache.selected_rows));
  setText("cache-min", formatDate(localCache.selected_min_time || localCache.min_time));
  setText("cache-max", localCache.selected_max_time ? `bis ${formatDate(localCache.selected_max_time)}` : "keine Stationsdaten");
  setText("cache-modified", formatDate(localCache.last_modified));
  setPill("cache-stale", cacheTone, !localCache.exists ? "fehlt" : localCache.stale ? "veraltet" : "aktuell");

  const strundeTone = !strunde.exists ? "bad" : strunde.stale ? "warn" : "good";
  setPill("strunde-stale", strundeTone, !strunde.exists ? "fehlt" : strunde.stale ? "veraltet" : "aktuell");
  setText("strunde-latest-level", centimeters(strunde.latest_level_cm));
  setText("strunde-max-time", formatDate(strunde.max_time));
  setText("strunde-min-time", formatDate(strunde.min_time));
  setText("strunde-rows", number(strunde.rows));
  setText("strunde-level-range", strunde.min_level_cm == null || strunde.max_level_cm == null ? "-" : `${centimeters(strunde.min_level_cm)} bis ${centimeters(strunde.max_level_cm)}`);

  renderComparison(comparison);
  renderModels(models);
  renderWarnings(live.warnings || []);
}

function renderComparison(comparison) {
  const pairCount = Number(comparison.pairs || 0);
  setText("compare-pairs", `${number(pairCount)} Paare`);
  setPill("compare-ready", pairCount > 0 ? "good" : "warn", pairCount > 0 ? "berechnet" : "keine Paare");
  const host = $("comparison-metrics");
  const summary = comparison.summary || {};
  const keys = Object.keys(summary);
  if (!keys.length) {
    host.replaceChildren(el("div", { className: "empty" }, comparison.computed === false ? "Noch nicht berechnet. Nutze /compare." : "Noch keine verwertbaren Forecast-vs-Ist-Paare."));
    return;
  }
  host.replaceChildren(
    ...keys.map((key) => {
      const item = summary[key] || {};
      return el(
        "div",
        { className: "metric-item" },
        el("div", { className: "metric-head" }, el("strong", {}, label(key)), el("span", { className: "pill info" }, `${number(item.count)} Paare`)),
        el(
          "div",
          { className: "metric-grid" },
          metric("MAE", round(item.mae)),
          metric("RMSE", round(item.rmse)),
          metric("Bias", signed(item.bias))
        )
      );
    })
  );
}

function renderModels(models) {
  const files = models.files || [];
  setText("model-files", `${files.length} Dateien`);
  const trainability = models.trainability || {};
  const keys = Object.keys(trainability);
  const readyCount = keys.filter((key) => trainability[key]?.ready).length;
  const trainedCount = keys.filter((key) => trainability[key]?.trained).length;
  setPill("model-readiness", readyCount > 0 ? "good" : "warn", `${readyCount} trainierbar`);
  const host = $("model-grid");
  if (!keys.length) {
    host.replaceChildren(el("div", { className: "empty" }, "Keine Modellinformationen vorhanden."));
    return;
  }
  host.replaceChildren(
    ...keys.map((key) => {
      const item = trainability[key] || {};
      const tone = item.trained ? "good" : item.ready ? "warn" : "bad";
      const stateText = item.trained ? "trainiert" : item.ready ? "bereit" : "zu wenig Daten";
      const latest = item.latest_model?.last_modified ? formatDate(item.latest_model.last_modified) : "kein Modell";
      return el(
        "div",
        { className: `model-item ${tone}` },
        el("strong", {}, label(key)),
        el("span", { className: `pill ${tone}` }, stateText),
        el("span", {}, `${number(item.points)} / ${number(item.required)} Punkte`),
        el("span", {}, latest)
      );
    })
  );
  setText("model-files", `${files.length} Dateien, ${trainedCount} aktiv`);
}

function renderWarnings(warnings) {
  setText("warning-count", String(warnings.length));
  const host = $("warnings");
  if (!warnings.length) {
    host.replaceChildren(el("li", { className: "empty" }, "Keine Warnungen."));
    return;
  }
  host.replaceChildren(...warnings.map((warning) => el("li", {}, warning)));
}

function renderJobs(jobs) {
  const allJobs = [...jobs].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  const active = allJobs.filter((job) => ["queued", "running"].includes(job.status)).length;
  setText("jobs-state", `${active} aktiv`);
  setText("job-count", String(allJobs.length));
  const host = $("job-list");
  if (!allJobs.length) {
    host.replaceChildren(el("div", { className: "empty" }, "Keine Jobs gestartet."));
    return;
  }
  host.replaceChildren(
    ...allJobs.slice(0, 8).map((job) => {
      const tone = job.status === "succeeded" && !job.result?.warning ? "good" : job.status === "failed" ? "bad" : "warn";
      return el(
        "div",
        { className: "job-item" },
        el("div", { className: "job-head" }, el("strong", {}, job.name), el("span", { className: `pill ${tone}` }, job.status)),
        el("span", {}, job.id),
        el("span", {}, formatDate(job.created_at)),
        el("span", {}, `Laufzeit ${formatSeconds(job.duration_seconds)}`)
      );
    })
  );
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = options.timeout || API_TIMEOUT_MS;
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      method: options.method || "GET",
      headers: { "Content-Type": "application/json" },
      body: options.body,
      signal: controller.signal,
    });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : {};
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    return payload;
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("Zeitlimit erreicht.");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function remember(value) {
  state.history.push(value);
  state.historyIndex = state.history.length;
}

function recallHistory(direction) {
  if (!state.history.length) return;
  state.historyIndex = Math.max(0, Math.min(state.history.length, state.historyIndex + direction));
  const value = state.history[state.historyIndex] || "";
  $("terminal-input").value = value;
  $("chat-input").value = value;
}

function appendLine(kind, text) {
  appendTerminalLine(kind, text);
  appendChatLine(kind, text);
}

function appendToSource(source, kind, text) {
  if (source === "chat") {
    appendChatLine(kind, text);
    return;
  }
  appendTerminalLine(kind, text);
}

function appendTerminalLine(kind, text) {
  const output = $("terminal-output");
  if (!output) return;
  const line = el("div", { className: `line ${kind}` });
  appendFormattedText(line, text);
  output.appendChild(line);
  output.scrollTop = output.scrollHeight;
}

function appendChatLine(kind, text) {
  const output = $("chat-output");
  if (!output) return;
  const displayText = kind === "user" ? String(text).replace(/^>\s*/, "") : text;
  const line = el("div", { className: `line ${kind}` });
  appendFormattedText(line, displayText);
  output.appendChild(line);
  output.scrollTop = output.scrollHeight;
}

function clearOutputs(source = "all") {
  const outputs = source === "chat" ? [$("chat-output")] : source === "ops" ? [$("terminal-output")] : [$("terminal-output"), $("chat-output")];
  outputs.forEach((output) => {
    if (output) output.replaceChildren();
  });
}

function appendFormattedText(target, value) {
  const parts = String(value).split(/(\*\*[^*\n]+\*\*)/g);
  parts.forEach((part) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      target.appendChild(el("strong", {}, part.slice(2, -2)));
    } else if (part) {
      target.appendChild(document.createTextNode(part));
    }
  });
}

function setTerminalState(value) {
  setText("terminal-state", value);
}

function setChip(id, tone, text) {
  $(id).replaceChildren(el("span", { className: `dot ${tone}` }), document.createTextNode(text));
}

function setDot(id, tone) {
  $(id).className = `dot ${tone}`;
}

function setPill(id, tone, text) {
  const element = $(id);
  element.className = `pill ${tone}`;
  element.textContent = text;
}

function setSignal(cardId, tone, valueId, text) {
  const card = $(cardId);
  card.className = `signal-card ${tone}`;
  setText(valueId, text);
}

function setText(id, value) {
  const element = $(id);
  if (element) {
    element.textContent = value ?? "-";
  }
}

function metric(name, value) {
  return el("span", {}, name, el("span", { className: "metric-value" }, value));
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([key, value]) => {
    if (key === "className") node.className = value;
    else node.setAttribute(key, value);
  });
  children.flat().forEach((child) => {
    if (child === null || child === undefined) return;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  });
  return node;
}

function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("de-DE", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

function number(value) {
  if (value === null || value === undefined || value === "") return "-";
  const parsed = Number(value || 0);
  return new Intl.NumberFormat("de-DE").format(parsed);
}

function round(value) {
  if (value === null || value === undefined || value === "") return "-";
  const parsed = Number(value || 0);
  return parsed.toLocaleString("de-DE", { maximumFractionDigits: 2 });
}

function signed(value) {
  if (value === null || value === undefined || value === "") return "-";
  const parsed = Number(value || 0);
  return `${parsed > 0 ? "+" : ""}${parsed.toLocaleString("de-DE", { maximumFractionDigits: 2 })}`;
}

function bytes(value) {
  const parsed = Number(value || 0);
  if (!parsed) return "-";
  if (parsed < 1024) return `${parsed} B`;
  if (parsed < 1024 * 1024) return `${(parsed / 1024).toLocaleString("de-DE", { maximumFractionDigits: 1 })} KB`;
  return `${(parsed / 1024 / 1024).toLocaleString("de-DE", { maximumFractionDigits: 1 })} MB`;
}

function centimeters(value) {
  if (value === null || value === undefined || value === "") return "-";
  const parsed = Number(value || 0);
  if (Number.isNaN(parsed)) return "-";
  return `${parsed.toLocaleString("de-DE", { maximumFractionDigits: 1 })} cm`;
}

function label(key) {
  return labels[key] || key;
}

function normalize(value) {
  return String(value)
    .trim()
    .toLowerCase()
    .replaceAll("\u00e4", "ae")
    .replaceAll("\u00f6", "oe")
    .replaceAll("\u00fc", "ue")
    .replaceAll("\u00df", "ss");
}

function recordTiming(labelText, durationMs, kind) {
  state.timings.unshift({
    label: labelText,
    durationMs,
    kind,
    at: new Date().toISOString(),
  });
  state.timings = state.timings.slice(0, 10);
  renderTimings();
}

function renderTimings() {
  setText("timing-count", String(state.timings.length));
  const host = $("timing-list");
  if (!state.timings.length) {
    host.replaceChildren(el("div", { className: "empty" }, "Noch keine Kommandos gemessen."));
    return;
  }
  host.replaceChildren(
    ...state.timings.map((item) =>
      el(
        "div",
        { className: "timing-item" },
        el("strong", {}, item.label),
        el("span", {}, `${formatDuration(item.durationMs)} - ${item.kind} - ${formatDate(item.at)}`)
      )
    )
  );
}

function formatDuration(durationMs) {
  const seconds = Number(durationMs || 0) / 1000;
  return formatSeconds(seconds);
}

function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "-";
  const parsed = Math.max(0, Number(seconds));
  if (parsed < 60) {
    return `${parsed.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}s`;
  }
  const minutes = Math.floor(parsed / 60);
  const rest = parsed - minutes * 60;
  return `${minutes}m ${rest.toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}s`;
}
