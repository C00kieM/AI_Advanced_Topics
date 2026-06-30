const state = {
  history: [],
  historyIndex: -1,
  jobLogCounts: new Map(),
  polling: new Map(),
  timings: [],
};

const labels = {
  temperature: "Temperatur",
  precipitation: "Niederschlag",
  wind_speed: "Wind",
};

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", () => {
  bindUi();
  bootTerminal();
  refreshStatus(false);
  refreshJobs();
});

function bindUi() {
  $("terminal-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("terminal-input");
    const value = input.value.trim();
    if (!value) return;
    input.value = "";
    runTerminalInput(value);
  });

  $("terminal-input").addEventListener("keydown", (event) => {
    if (event.key === "ArrowUp") {
      event.preventDefault();
      recallHistory(-1);
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      recallHistory(1);
    }
  });

  document.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => runTerminalInput(button.dataset.command));
  });

  $("refresh-status").addEventListener("click", () => refreshStatus(true));
}

function bootTerminal() {
  appendLine("system", "Weather Ops Leitstand bereit.");
  appendLine(
    "system",
    "Erlaubte Kommandos: /status, /sync-local, /sync-dwd, /archive, /compare, /train, /clear. Keine Betriebssystem-Shell."
  );
}

async function runTerminalInput(value) {
  remember(value);
  appendLine("user", `> ${value}`);
  if (value === "/clear") {
    $("terminal-output").replaceChildren();
    bootTerminal();
    return;
  }
  setTerminalState("arbeitet");
  const startedAt = performance.now();
  try {
    if (value.startsWith("/")) {
      await runCommand(value);
    } else {
      await askChat(value);
    }
  } catch (error) {
    appendLine("error", error.message || String(error));
  } finally {
    const durationMs = performance.now() - startedAt;
    recordTiming(value.startsWith("/") ? value : "Chat", durationMs, "terminal");
    appendLine("system", `Dauer ${formatDuration(durationMs)} fuer ${value.startsWith("/") ? value : "Chat"}.`);
    setTerminalState("bereit");
  }
}

async function runCommand(command) {
  const response = await api("/terminal/command", {
    method: "POST",
    body: JSON.stringify({ command }),
  });
  if (response.type === "clear") return;
  if (response.type === "status") {
    renderStatus(response.status);
    appendLine("success", "Status aktualisiert.");
    return;
  }
  if (response.type === "comparison") {
    renderComparison(response.comparison);
    appendLine("system", formatJson(response.comparison));
    return;
  }
  if (response.type === "job") {
    announceJob(response.job);
    pollJob(response.job.id);
  }
}

async function askChat(question) {
  const response = await api("/chat", {
    method: "POST",
    body: JSON.stringify({ question }),
  });
  appendLine("system", response.answer);
}

async function refreshStatus(live) {
  setTerminalState(live ? "live" : "status");
  try {
    const status = await api(`/status?live=${live ? "true" : "false"}`, { timeout: live ? 65000 : 10000 });
    renderStatus(status);
    if (status.live?.warnings?.length) {
      appendLine("warning", `Status mit ${status.live.warnings.length} Warnung(en) aktualisiert.`);
    }
  } catch (error) {
    appendLine("error", `Status konnte nicht geladen werden: ${error.message || error}`);
  } finally {
    setTerminalState("bereit");
  }
}

async function refreshJobs() {
  try {
    const payload = await api("/jobs", { timeout: 10000 });
    renderJobs(payload.jobs || []);
  } catch (error) {
    appendLine("error", `Jobs konnten nicht geladen werden: ${error.message || error}`);
  }
}

function announceJob(job) {
  appendLine("system", `Job ${job.id} gestartet: ${job.name}`);
  renderJobs([job]);
}

function pollJob(jobId) {
  if (state.polling.has(jobId)) return;
  const timer = setInterval(async () => {
    try {
      const job = await api(`/jobs/${jobId}`, { timeout: 10000 });
      renderJobLogs(job);
      await refreshJobs();
      if (["succeeded", "failed"].includes(job.status)) {
        clearInterval(timer);
        state.polling.delete(jobId);
        if (job.status === "succeeded") {
          if (job.result?.warning) {
            appendLine("warning", `Job ${job.id} abgeschlossen in ${formatSeconds(job.duration_seconds)} mit Warnung: ${job.result.warning}`);
          } else {
            appendLine("success", `Job ${job.id} abgeschlossen in ${formatSeconds(job.duration_seconds)}.`);
          }
        } else {
          appendLine("error", `Job ${job.id} fehlgeschlagen nach ${formatSeconds(job.duration_seconds)}: ${job.error}`);
        }
        recordTiming(job.name, (job.duration_seconds || 0) * 1000, "job");
        refreshStatus(false);
      }
    } catch (error) {
      appendLine("error", `Job ${jobId} konnte nicht gelesen werden: ${error.message || error}`);
      clearInterval(timer);
      state.polling.delete(jobId);
    }
  }, 1200);
  state.polling.set(jobId, timer);
}

function renderJobLogs(job) {
  const previous = state.jobLogCounts.get(job.id) || 0;
  const nextLogs = (job.logs || []).slice(previous);
  nextLogs.forEach((line) => appendLine(job.status === "failed" ? "error" : "system", `${job.id} ${line}`));
  state.jobLogCounts.set(job.id, (job.logs || []).length);
}

function renderStatus(payload) {
  const live = payload.live || {};
  const config = payload.config || {};
  const localCache = payload.local_cache || {};
  const dwd = payload.dwd_data || {};
  const comparison = payload.comparison || {};
  const models = payload.models || {};
  const liveChecked = live.checked !== false;
  const influxTone = liveChecked ? (live.influx_ok ? "good" : "bad") : "unknown";
  const dwdTone = liveChecked ? (live.dwd_ok ? "good" : "bad") : "unknown";
  const cacheTone = !localCache.exists ? "bad" : localCache.stale ? "warn" : "good";
  const forecastCount = Number(dwd.forecast_rows || 0);
  const pairCount = Number(comparison.pairs || 0);

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
  setText("forecast-valid", forecastCount > 0 ? `gueltig bis ${formatDate(dwd.max_valid_at)}` : "keine Forecasts archiviert");
  setText("dwd-size", bytes(dwd.size_bytes));

  setText("cache-rows", number(localCache.rows));
  setText("cache-selected-rows", number(localCache.selected_rows));
  setText("cache-min", formatDate(localCache.selected_min_time || localCache.min_time));
  setText("cache-max", localCache.selected_max_time ? `bis ${formatDate(localCache.selected_max_time)}` : "keine Stationsdaten");
  setText("cache-modified", formatDate(localCache.last_modified));
  setPill("cache-stale", cacheTone, !localCache.exists ? "fehlt" : localCache.stale ? "veraltet" : "aktuell");

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
  const timeout = options.timeout || 30000;
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
  $("terminal-input").value = state.history[state.historyIndex] || "";
}

function appendLine(kind, text) {
  const output = $("terminal-output");
  const line = el("div", { className: `line ${kind}` });
  appendFormattedText(line, text);
  output.appendChild(line);
  output.scrollTop = output.scrollHeight;
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

function label(key) {
  return labels[key] || key;
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
