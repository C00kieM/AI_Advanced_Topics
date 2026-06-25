const state = {
  history: [],
  historyIndex: -1,
  jobLogCounts: new Map(),
  polling: new Map(),
  timings: [],
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

  $("refresh-status").addEventListener("click", () => runTerminalInput("/status"));
}

function bootTerminal() {
  appendLine("system", "Weather Ops Terminal bereit.");
  appendLine(
    "system",
    "Erlaubte Kommandos: /status, /sync-local, /sync-dwd, /archive, /compare, /train, /clear. Freitext geht an den lokalen Chat."
  );
}

async function runTerminalInput(value) {
  remember(value);
  appendLine("user", `> ${value}`);
  if (value === "/clear") {
    $("terminal-output").innerHTML = "";
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
  setTerminalState("status");
  try {
    const status = await api(`/status?live=${live ? "true" : "false"}`, { timeout: live ? 65000 : 90000 });
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

  setChip("chip-influx", liveChecked ? (live.influx_ok ? "good" : "bad") : "unknown", "InfluxDB");
  setChip("chip-dwd", liveChecked ? (live.dwd_ok ? "good" : "bad") : "unknown", "DWD");
  setDot("influx-dot", liveChecked ? (live.influx_ok ? "good" : "bad") : "unknown");
  setDot("dwd-dot", liveChecked ? (live.dwd_ok ? "good" : "bad") : "unknown");

  $("status-generated").textContent = formatDate(payload.generated_at);
  $("influx-state").textContent = liveChecked ? (live.influx_ok ? "OK" : "Fehler") : "nicht geprüft";
  $("dwd-state").textContent = liveChecked ? (live.dwd_ok ? "OK" : "Fehler") : "nicht geprüft";
  $("local-state").textContent = localCache.stale ? "veraltet" : "aktuell";

  $("influx-url").textContent = config.influx_url || "-";
  $("influx-bucket").textContent = config.influx_bucket || "-";
  $("local-measurement").textContent = config.local_measurement || "-";
  $("influx-token").textContent = config.influx_token || "-";
  $("mosmix-station").textContent = config.mosmix_station_id || config.dwd_station_id || "-";
  $("mosmix-product").textContent = config.mosmix_product || "-";
  $("forecast-rows").textContent = number(dwd.forecast_rows);
  $("forecast-valid").textContent = formatDate(dwd.max_valid_at);

  $("cache-rows").textContent = number(localCache.rows);
  $("cache-min").textContent = formatDate(localCache.min_time);
  $("cache-max").textContent = formatDate(localCache.max_time);
  $("cache-modified").textContent = formatDate(localCache.last_modified);
  setPill("cache-stale", localCache.stale ? "warn" : "good", localCache.stale ? "veraltet" : "aktuell");

  renderComparison(comparison);
  renderModels(models);
  renderWarnings(live.warnings || []);
}

function renderComparison(comparison) {
  $("compare-pairs").textContent = `${number(comparison.pairs || 0)} Paare`;
  const host = $("comparison-metrics");
  const summary = comparison.summary || {};
  const keys = Object.keys(summary);
  if (!keys.length) {
    host.innerHTML = `<div class="empty">${comparison.computed === false ? "Noch nicht berechnet. Nutze /compare." : "Noch keine verwertbaren Forecast-vs-Ist-Paare."}</div>`;
    return;
  }
  host.innerHTML = keys
    .map((key) => {
      const item = summary[key];
      return `<div class="metric-item"><strong>${label(key)}</strong><span>Count ${number(item.count)} - MAE ${round(item.mae)} - Bias ${round(item.bias)}</span></div>`;
    })
    .join("");
}

function renderModels(models) {
  const files = models.files || [];
  $("model-files").textContent = `${files.length} Dateien`;
  const trainability = models.trainability || {};
  const host = $("model-grid");
  const keys = Object.keys(trainability);
  if (!keys.length) {
    host.innerHTML = `<div class="empty">Keine Modellinformationen vorhanden.</div>`;
    return;
  }
  host.innerHTML = keys
    .map((key) => {
      const item = trainability[key];
      const className = item.ready ? "good" : "warn";
      const text = item.ready ? "trainierbar" : "zu wenig Daten";
      return `<div class="model-item"><strong>${label(key)}</strong><span class="pill ${className}">${text}</span><span>${number(item.points)} / ${number(item.required)} Punkte</span></div>`;
    })
    .join("");
}

function renderWarnings(warnings) {
  $("warning-count").textContent = String(warnings.length);
  const host = $("warnings");
  if (!warnings.length) {
    host.innerHTML = `<li class="empty">Keine Warnungen.</li>`;
    return;
  }
  host.innerHTML = warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
}

function renderJobs(jobs) {
  const allJobs = mergeJobs(jobs);
  const active = allJobs.filter((job) => ["queued", "running"].includes(job.status)).length;
  $("jobs-state").textContent = `${active} aktiv`;
  $("job-count").textContent = String(allJobs.length);
  const host = $("job-list");
  if (!allJobs.length) {
    host.innerHTML = `<div class="empty">Keine Jobs gestartet.</div>`;
    return;
  }
  host.innerHTML = allJobs
    .slice(0, 8)
    .map((job) => {
      const statusClass = job.status === "succeeded" && !job.result?.warning ? "good" : job.status === "failed" ? "bad" : "warn";
      return `<div class="job-item"><strong>${escapeHtml(job.name)}</strong><span class="pill ${statusClass}">${job.status}</span><span>${job.id} - ${formatDate(job.created_at)}</span><span>Laufzeit ${formatSeconds(job.duration_seconds)}</span></div>`;
    })
    .join("");
}

function mergeJobs(newJobs) {
  const existing = new Map();
  document.querySelectorAll(".job-item").forEach(() => {});
  return [...newJobs].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
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
  const line = document.createElement("div");
  line.className = `line ${kind}`;
  line.innerHTML = renderTerminalText(text);
  output.appendChild(line);
  output.scrollTop = output.scrollHeight;
}

function setTerminalState(value) {
  $("terminal-state").textContent = value;
}

function setChip(id, stateName, labelText) {
  const element = $(id);
  element.innerHTML = `<span class="dot ${stateName}"></span>${labelText}`;
}

function setDot(id, stateName) {
  const element = $(id);
  element.className = `dot ${stateName}`;
}

function setPill(id, stateName, text) {
  const element = $(id);
  element.className = `pill ${stateName}`;
  element.textContent = text;
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
  const parsed = Number(value || 0);
  return parsed.toLocaleString("de-DE", { maximumFractionDigits: 2 });
}

function label(key) {
  return (
    {
      temperature: "Temperatur",
      precipitation: "Niederschlag",
      wind_speed: "Wind",
    }[key] || key
  );
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderTerminalText(value) {
  return escapeHtml(value).replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
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
  $("timing-count").textContent = String(state.timings.length);
  const host = $("timing-list");
  if (!state.timings.length) {
    host.innerHTML = `<div class="empty">Noch keine Kommandos gemessen.</div>`;
    return;
  }
  host.innerHTML = state.timings
    .map(
      (item) =>
        `<div class="timing-item"><strong>${escapeHtml(item.label)}</strong><span>${formatDuration(item.durationMs)} - ${escapeHtml(item.kind)} - ${formatDate(item.at)}</span></div>`
    )
    .join("");
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
