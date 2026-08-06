"use strict";

const state = { payload: null, airports: [], activeJob: null };
const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body) {
    headers["Content-Type"] = "application/json";
    headers["X-AutoFly-Request"] = "dashboard";
  }
  const response = await fetch(path, { ...options, headers });
  const payload = response.headers.get("content-type")?.includes("json")
    ? await response.json()
    : null;
  if (!response.ok) throw new Error(payload?.detail || `Request failed (${response.status})`);
  return payload;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function money(value, currency) {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(Number(value)); }
  catch { return `${currency} ${value}`; }
}

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.classList.add("show");
  window.setTimeout(() => element.classList.remove("show"), 3500);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function dateDescription(watch) {
  const dates = watch.dates;
  if (dates.mode === "exact") return dates.return ? `${dates.departure} → ${dates.return}` : dates.departure;
  if (dates.mode === "range") return `${dates.departure_start} through ${dates.departure_end}`;
  return `Day ${dates.days_from_now} through day ${dates.days_to}`;
}

function renderState(payload) {
  state.payload = payload;
  const active = payload.watches.filter((watch) => watch.enabled);
  const routes = active.reduce((total, watch) => total + watch.origins.length * watch.destinations.length, 0);
  $("stat-watches").textContent = active.length;
  $("stat-routes").textContent = `${routes} route pair${routes === 1 ? "" : "s"}`;
  $("stat-fares").textContent = payload.summary.available_itineraries;
  $("stat-alerts").textContent = payload.summary.successful_notifications;
  $("stat-health").textContent = payload.summary.consecutive_failures ? "Needs attention" : "Healthy";
  $("stat-health-detail").textContent = payload.summary.consecutive_failures
    ? `${payload.summary.consecutive_failures} consecutive failed cycle(s)`
    : "No consecutive failures";
  $("hero-copy").textContent = active.length
    ? `${routes} route pairs across ${active.length} active watch${active.length === 1 ? "" : "es"}. AutoFly alerts only when every rule matches.`
    : "No watches are active. Enable one below to begin monitoring.";
  $("schedule-value").textContent = `${payload.scheduler.interval_hours} hours`;
  $("schedule-detail").textContent = `Plus up to ${payload.scheduler.jitter_minutes} minutes of randomized delay · ${payload.scheduler.timezone}`;
  const source = $("source-status");
  source.textContent = payload.source.available ? "Flight source ready" : "Flight source unavailable";
  source.className = `status-pill ${payload.source.available ? "good" : "bad"}`;
  renderWatches(payload.watches);
  renderCycles(payload.cycles);
  updateHistoryFilter(payload.watches);
  const running = payload.jobs.find((job) => ["queued", "running"].includes(job.status));
  if (running) beginJobPolling(running.id);
}

function renderWatches(watches) {
  const list = $("watch-list");
  list.replaceChildren();
  if (!watches.length) {
    list.append(element("p", "empty-state", "No watches yet. Create your first route."));
    return;
  }
  for (const watch of watches) {
    const card = element("article", `watch-card${watch.enabled ? "" : " disabled"}`);
    const header = element("div", "watch-header");
    header.append(element("h3", "", watch.id));
    const toggle = element("button", `toggle${watch.enabled ? " on" : ""}`);
    toggle.type = "button";
    toggle.setAttribute("aria-label", `${watch.enabled ? "Disable" : "Enable"} ${watch.id}`);
    toggle.addEventListener("click", () => toggleWatch(watch));
    header.append(toggle);
    const route = element("div", "route-line");
    route.append(element("span", "", watch.origins.join(" · ")));
    route.append(element("span", "route-arrow", "→"));
    route.append(element("span", "", watch.destinations.join(" · ")));
    const meta = element("div", "watch-meta");
    [
      dateDescription(watch),
      money(watch.deal.maximum_price, watch.deal.currency),
      watch.trip.cabin.replace("_", " "),
      watch.deal.max_stops === null ? "Any stops" : `${watch.deal.max_stops} stop max`,
    ].forEach((value) => meta.append(element("span", "", value)));
    const actions = element("div", "watch-actions");
    const edit = element("button", "button ghost small", "Edit");
    edit.type = "button";
    edit.addEventListener("click", () => openWatchDialog(watch));
    const check = element("button", "button ghost small", "Check now");
    check.type = "button";
    check.disabled = !watch.enabled;
    check.addEventListener("click", () => startCheck(watch.id));
    actions.append(edit, check);
    card.append(header, route, meta, actions);
    list.append(card);
  }
}

function renderCycles(cycles) {
  const list = $("cycle-list");
  list.replaceChildren();
  if (!cycles.length) {
    list.append(element("p", "empty-state", "No search cycles recorded yet."));
    return;
  }
  for (const cycle of cycles) {
    const row = element("div", "cycle-row");
    const left = element("span", "");
    const status = element("span", `status-pill ${cycle.status === "success" ? "good" : "bad"}`, cycle.status.replace("_", " "));
    left.append(status);
    const metrics = cycle.metrics || {};
    row.append(left, element("span", "", formatDate(cycle.started_at)), element("span", "", `${metrics.successful_searches || 0} searches · ${metrics.candidate_count || 0} candidates`));
    list.append(row);
  }
}

function updateHistoryFilter(watches) {
  const select = $("history-watch");
  const current = select.value;
  select.replaceChildren(new Option("All watches", ""));
  watches.forEach((watch) => select.add(new Option(watch.id, watch.id)));
  select.value = watches.some((watch) => watch.id === current) ? current : "";
}

async function loadHistory() {
  const watch = $("history-watch").value;
  const rows = await api(`/api/history?limit=50${watch ? `&watch_id=${encodeURIComponent(watch)}` : ""}`);
  const body = $("history-body");
  body.replaceChildren();
  $("history-empty").hidden = rows.length > 0;
  for (const item of rows) {
    const row = document.createElement("tr");
    const values = [
      formatDate(item.observed_at),
      `${item.origin || "?"} → ${item.destination || "?"}`,
      formatDate(item.departure_at),
      item.airline || "Unknown",
      item.stops === null ? "Unknown" : item.stops === 0 ? "Direct" : String(item.stops),
    ];
    values.forEach((value) => row.append(element("td", "", value)));
    row.append(element("td", "price", money(item.price, item.currency)));
    const action = element("td", "");
    if (item.booking_url) {
      const link = element("a", "", "Open fare");
      link.href = item.booking_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      action.append(link);
    }
    row.append(action);
    body.append(row);
  }
}

async function refresh() {
  $("refresh-button").disabled = true;
  try {
    const [payload] = await Promise.all([api("/api/state"), loadAirports()]);
    renderState(payload);
    await loadHistory();
  } catch (error) { toast(error.message); }
  finally { $("refresh-button").disabled = false; }
}

async function loadAirports() {
  if (state.airports.length) return;
  state.airports = await api("/api/airports");
}

function initAirportAutocomplete(inputId) {
  const input = $(inputId);
  const panel = $(`${inputId}-suggestions`);
  const close = () => panel.classList.remove("open");
  input.addEventListener("input", () => {
    const query = input.value.split(",").at(-1).trim().toUpperCase();
    panel.replaceChildren();
    if (!query) { close(); return; }
    const matches = state.airports.filter((airport) => airport.code.startsWith(query) || airport.name.toUpperCase().includes(query)).slice(0, 6);
    for (const airport of matches) {
      const button = document.createElement("button");
      button.type = "button";
      const code = element("strong", "", airport.code);
      const name = element("small", "", airport.name);
      button.append(code, name);
      button.addEventListener("click", () => {
        const parts = input.value.split(",");
        parts[parts.length - 1] = ` ${airport.code}`;
        input.value = parts.map((part) => part.trim()).filter(Boolean).join(", ");
        close();
        input.focus();
      });
      panel.append(button);
    }
    panel.classList.toggle("open", matches.length > 0);
  });
  input.addEventListener("blur", () => window.setTimeout(close, 150));
}

function setDateMode(mode) {
  ["exact", "range", "rolling"].forEach((value) => { $(`dates-${value}`).hidden = value !== mode; });
  if ($("trip-type").value === "round_trip" && mode !== "exact") {
    $("date-mode").value = "exact";
    setDateMode("exact");
  }
  $("return-field").hidden = $("trip-type").value !== "round_trip";
}

function openWatchDialog(watch = null) {
  $("watch-form").reset();
  $("watch-enabled").checked = true;
  $("adults").value = "1";
  $("currency").value = "USD";
  $("max-stops").value = "1";
  $("cooldown").value = "24";
  $("date-mode").value = "range";
  $("form-error").textContent = "";
  $("original-id").value = watch?.id || "";
  $("dialog-title").textContent = watch ? `Edit ${watch.id}` : "New flight watch";
  if (watch) {
    $("watch-id").value = watch.id;
    $("watch-enabled").checked = watch.enabled;
    $("origins").value = watch.origins.join(", ");
    $("destinations").value = watch.destinations.join(", ");
    $("trip-type").value = watch.trip.type;
    $("cabin").value = watch.trip.cabin;
    $("adults").value = watch.trip.adults;
    $("date-mode").value = watch.dates.mode;
    if (watch.dates.mode === "exact") {
      $("exact-departure").value = watch.dates.departure;
      $("exact-return").value = watch.dates.return || "";
    } else if (watch.dates.mode === "range") {
      $("range-start").value = watch.dates.departure_start;
      $("range-end").value = watch.dates.departure_end;
    } else {
      $("rolling-start").value = watch.dates.days_from_now;
      $("rolling-end").value = watch.dates.days_to;
    }
    $("currency").value = watch.deal.currency;
    $("maximum-price").value = watch.deal.maximum_price;
    $("max-stops").value = watch.deal.max_stops ?? "";
    $("max-layover").value = watch.deal.max_layover_hours ?? "";
    $("self-transfer").checked = watch.deal.allow_self_transfer;
    $("cooldown").value = watch.notifications.cooldown_hours;
  }
  setDateMode($("date-mode").value);
  $("watch-dialog").showModal();
  $("watch-dialog").scrollTop = 0;
}

function codes(value) { return value.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean); }

function watchPayload() {
  const mode = $("date-mode").value;
  let dates;
  if (mode === "exact") {
    dates = { mode, departure: $("exact-departure").value };
    if ($("trip-type").value === "round_trip") dates.return = $("exact-return").value;
  } else if (mode === "range") {
    dates = { mode, departure_start: $("range-start").value, departure_end: $("range-end").value };
  } else {
    dates = { mode, days_from_now: Number($("rolling-start").value), days_to: Number($("rolling-end").value) };
  }
  const deal = {
    currency: $("currency").value.toUpperCase(),
    maximum_price: Number($("maximum-price").value),
    max_stops: $("max-stops").value === "" ? null : Number($("max-stops").value),
    allow_self_transfer: $("self-transfer").checked,
  };
  if ($("max-layover").value) deal.max_layover_hours = Number($("max-layover").value);
  return {
    id: $("watch-id").value.trim(), enabled: $("watch-enabled").checked,
    origins: codes($("origins").value), destinations: codes($("destinations").value),
    trip: { type: $("trip-type").value, adults: Number($("adults").value), cabin: $("cabin").value },
    dates, deal,
    notifications: { cooldown_hours: Number($("cooldown").value), alert_on_price_drop: { amount: 1 } },
  };
}

async function saveWatch(event) {
  event.preventDefault();
  const original = $("original-id").value;
  try {
    const watch = watchPayload();
    await api(original ? `/api/watches/${encodeURIComponent(original)}` : "/api/watches", {
      method: original ? "PUT" : "POST",
      body: JSON.stringify({ watch, original_id: original || null }),
    });
    $("watch-dialog").close();
    toast(`Saved ${watch.id}`);
    await refresh();
  } catch (error) { $("form-error").textContent = error.message; }
}

async function toggleWatch(watch) {
  try {
    await api(`/api/watches/${encodeURIComponent(watch.id)}/enabled`, { method: "POST", body: JSON.stringify({ enabled: !watch.enabled }) });
    toast(`${watch.id} ${watch.enabled ? "disabled" : "enabled"}`);
    await refresh();
  } catch (error) { toast(error.message); }
}

async function startCheck(watchId = null) {
  const scope = watchId || "all enabled watches";
  if (!window.confirm(`Run a live fare check for ${scope}? Notifications may be sent for qualifying deals.`)) return;
  const button = $("check-all-button");
  button.disabled = true;
  try {
    const job = await api("/api/checks", { method: "POST", body: JSON.stringify({ watch_id: watchId }) });
    $("check-feedback").textContent = "Check running…";
    beginJobPolling(job.id);
  } catch (error) { toast(error.message); button.disabled = false; }
}

function beginJobPolling(jobId) {
  if (state.activeJob === jobId) return;
  state.activeJob = jobId;
  const poll = async () => {
    try {
      const job = await api(`/api/checks/${encodeURIComponent(jobId)}`);
      if (["queued", "running"].includes(job.status)) {
        $("check-feedback").textContent = "Check running…";
        window.setTimeout(poll, 2000);
        return;
      }
      $("check-feedback").textContent = "";
      $("check-all-button").disabled = false;
      state.activeJob = null;
      toast(job.status === "completed" ? `Check finished: ${job.result?.status || "complete"}` : job.error || "Check failed");
      await refresh();
    } catch (error) {
      state.activeJob = null;
      $("check-all-button").disabled = false;
      toast(error.message);
    }
  };
  poll();
}

$("refresh-button").addEventListener("click", refresh);
$("check-all-button").addEventListener("click", () => startCheck());
$("add-watch-button").addEventListener("click", () => openWatchDialog());
$("history-watch").addEventListener("change", loadHistory);
$("date-mode").addEventListener("change", (event) => setDateMode(event.target.value));
$("trip-type").addEventListener("change", () => setDateMode($("date-mode").value));
$("watch-form").addEventListener("submit", saveWatch);
$("cancel-watch").addEventListener("click", () => $("watch-dialog").close());
initAirportAutocomplete("origins");
initAirportAutocomplete("destinations");

refresh();
