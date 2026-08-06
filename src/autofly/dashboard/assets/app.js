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
  if (!response.ok) {
    const detail = payload?.detail;
    if (Array.isArray(detail)) {
      const message = detail.map((issue) => `${issue.loc?.at(-1) || "field"}: ${issue.msg}`).join("\n");
      throw new Error(message);
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }
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
  if (dates.mode === "exact") return dates.return ? `${dates.departure} to ${dates.return}` : `Departs ${dates.departure}`;
  if (dates.mode === "range") return `Depart between ${dates.departure_start} and ${dates.departure_end}`;
  return `Always check ${dates.days_from_now}–${dates.days_to} days ahead`;
}

function readableName(value) {
  return value.replaceAll(/[-_]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function stopsDescription(value) {
  if (value === null) return "any number of stops";
  if (value === 0) return "direct flights only";
  return `up to ${value} stop${value === 1 ? "" : "s"}`;
}

function renderState(payload) {
  state.payload = payload;
  const active = payload.watches.filter((watch) => watch.enabled);
  const routes = active.reduce((total, watch) => total + watch.origins.length * watch.destinations.length, 0);
  $("stat-watches").textContent = active.length;
  $("stat-routes").textContent = `${routes} route pair${routes === 1 ? "" : "s"}`;
  $("stat-fares").textContent = payload.summary.available_itineraries;
  $("stat-alerts").textContent = payload.summary.successful_notifications;
  $("stat-health").textContent = payload.summary.consecutive_failures ? "Needs attention" : "All good";
  $("stat-health-detail").textContent = payload.summary.consecutive_failures
    ? `${payload.summary.consecutive_failures} consecutive failed cycle(s)`
    : "No recent search failures";
  $("hero-copy").textContent = active.length
    ? `Monitoring ${routes} route combination${routes === 1 ? "" : "s"} across ${active.length} active watch${active.length === 1 ? "" : "es"}. You’ll only be notified when every deal rule passes.`
    : "Nothing is being monitored yet. Start a watch below and AutoFly will take it from there.";
  $("schedule-value").textContent = `Every ${payload.scheduler.interval_hours} hours`;
  $("schedule-detail").textContent = `Runs automatically in ${payload.scheduler.timezone}. Start time may vary by up to ${payload.scheduler.jitter_minutes} minutes to keep searches respectful.`;
  const source = $("source-status");
  source.textContent = payload.source.available ? "Fare search ready" : "Fare search needs attention";
  source.className = `status-pill ${payload.source.available ? "good" : "bad"}`;
  setReadiness("guide-watch", payload.watches.length > 0, payload.watches.length ? `${payload.watches.length} watch${payload.watches.length === 1 ? "" : "es"} configured` : "Add a watch");
  setReadiness("guide-source", payload.source.available, payload.source.available ? "Fare search connected" : "Connect fare search");
  const notifierReady = payload.notifications.telegram || payload.notifications.webhook;
  setReadiness("guide-notifier", notifierReady, notifierReady ? "Notifications enabled" : "Enable notifications");
  renderWatches(payload.watches);
  renderCycles(payload.cycles);
  updateHistoryFilter(payload.watches);
  const running = payload.jobs.find((job) => ["queued", "running"].includes(job.status));
  if (running) beginJobPolling(running.id);
}

function setReadiness(id, ready, text) {
  const item = $(id);
  item.textContent = `${ready ? "✓" : "○"} ${text}`;
  item.classList.toggle("ready", ready);
}

function renderWatches(watches) {
  const list = $("watch-list");
  list.replaceChildren();
  if (!watches.length) {
    const empty = element("div", "empty-state");
    empty.append(element("strong", "", "No flight watches yet"));
    empty.append(element("p", "", "Create one to tell AutoFly where, when, and what price to monitor."));
    const create = element("button", "button secondary", "Create my first watch");
    create.type = "button";
    create.addEventListener("click", () => openWatchDialog());
    empty.append(create);
    list.append(empty);
    return;
  }
  for (const watch of watches) {
    const card = element("article", `watch-card${watch.enabled ? "" : " disabled"}`);
    const header = element("div", "watch-header");
    const title = element("div", "watch-title");
    title.append(element("h3", "", readableName(watch.id)));
    title.append(element("span", "watch-id", watch.id));
    header.append(title, element("span", `watch-state${watch.enabled ? "" : " paused"}`, watch.enabled ? "Monitoring" : "Paused"));
    const route = element("div", "route-line");
    route.append(element("span", "", watch.origins.join(" · ")));
    route.append(element("span", "route-arrow", "→"));
    route.append(element("span", "", watch.destinations.join(" · ")));
    const meta = element("div", "watch-meta");
    const routeCount = watch.origins.length * watch.destinations.length;
    [
      dateDescription(watch),
      `${routeCount} route${routeCount === 1 ? "" : "s"}`,
      readableName(watch.trip.cabin),
    ].forEach((value) => meta.append(element("span", "", value)));
    const rule = element("p", "watch-rule", `Alert below ${money(watch.deal.maximum_price, watch.deal.currency)} · ${stopsDescription(watch.deal.max_stops)} · ${watch.deal.allow_self_transfer ? "self-transfers allowed" : "no self-transfers"}`);
    const actions = element("div", "watch-actions");
    const edit = element("button", "button ghost small", "Edit watch");
    edit.type = "button";
    edit.addEventListener("click", () => openWatchDialog(watch));
    const check = element("button", "button ghost small", "Search now");
    check.type = "button";
    check.disabled = !watch.enabled;
    check.addEventListener("click", () => startCheck(watch.id));
    const toggle = element("button", "button ghost small", watch.enabled ? "Pause" : "Start monitoring");
    toggle.type = "button";
    toggle.addEventListener("click", () => toggleWatch(watch));
    actions.append(edit, toggle, check);
    card.append(header, route, meta, rule, actions);
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
    const cycleStatus = cycle.display_status || cycle.status;
    const status = element("span", `status-pill ${cycleStatus === "success" ? "good" : "bad"}`, cycleStatus === "success" ? "Completed" : readableName(cycleStatus));
    left.append(status);
    const metrics = cycle.metrics || {};
    const searches = metrics.successful_searches || 0;
    const fares = metrics.candidate_count || 0;
    row.append(left, element("span", "", formatDate(cycle.started_at)), element("span", "", `${searches} successful search${searches === 1 ? "" : "es"} · ${fares} fare${fares === 1 ? "" : "s"} reviewed`));
    list.append(row);
  }
}

function updateHistoryFilter(watches) {
  const select = $("history-watch");
  const current = select.value;
  select.replaceChildren(new Option("All watches", ""));
  watches.forEach((watch) => select.add(new Option(watch.id, watch.id)));
  select.value = watches.some((watch) => watch.id === current) ? current : "";
  updateRouteFilter();
}

async function loadHistory() {
  const watch = $("history-watch").value;
  const route = $("history-route").value.split("|");
  const params = new URLSearchParams({ limit: "50" });
  if (watch) params.set("watch_id", watch);
  if (route.length === 2) {
    params.set("origin", route[0]);
    params.set("destination", route[1]);
  }
  if ($("history-stops").value) params.set("max_stops", $("history-stops").value);
  if ($("history-airline").value.trim()) params.set("airline", $("history-airline").value.trim());
  if ($("history-deals").checked) params.set("qualifying", "true");
  const rows = await api(`/api/history?${params}`);
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
    const evaluation = qualificationDescription(item.qualifies, item.qualification_reason);
    const ruleCell = element("td", "evaluation-cell");
    const badge = element("span", `rule-result ${evaluation.className}`, evaluation.label);
    badge.title = evaluation.detail;
    ruleCell.append(badge, element("small", "rule-detail", evaluation.detail));
    row.append(ruleCell);
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
  await loadTrend();
}

function updateRouteFilter() {
  const select = $("history-route");
  const current = select.value;
  const watchId = $("history-watch").value;
  select.replaceChildren(new Option(watchId ? "All routes" : "Choose a watch first", ""));
  select.disabled = !watchId;
  const watch = state.payload?.watches.find((item) => item.id === watchId);
  if (!watch) return;
  for (const origin of watch.origins) {
    for (const destination of watch.destinations) {
      select.add(new Option(`${origin} → ${destination}`, `${origin}|${destination}`));
    }
  }
  select.value = [...select.options].some((option) => option.value === current) ? current : "";
}

function qualificationDescription(qualifies, reason) {
  if (qualifies === true) return { className: "deal", label: "Deal", detail: "Passed every watch rule." };
  if (qualifies === null) return { className: "unknown", label: "Not evaluated", detail: "Recorded before rule explanations were stored." };
  const reasons = {
    "route does not match watch": "Route does not match this watch.",
    "currency mismatch; AutoFly does not convert currency": "Currency differs from the watch; AutoFly does not convert it.",
    "price is not strictly below maximum_price": "Price is at or above your alert limit.",
    "stop limit exceeded or unknown": "Too many stops, or stop information is missing.",
    "layover limit exceeded or unknown": "A layover is too long, or layover information is missing.",
    "self-transfer status is true or unknown": "Self-transfer is required or could not be confirmed.",
    "no usable search link": "No usable booking or search link was supplied.",
  };
  return { className: "no-deal", label: "Rule missed", detail: reasons[reason] || "Did not pass every saved rule." };
}

async function loadTrend() {
  const watch = $("history-watch").value;
  const route = $("history-route").value.split("|");
  const chart = $("price-chart");
  const empty = $("trend-empty");
  chart.replaceChildren();
  if (!watch || route.length !== 2) {
    chart.hidden = true;
    empty.hidden = false;
    $("trend-title").textContent = "Choose a watch and route";
    $("trend-range").textContent = "";
    return;
  }
  const params = new URLSearchParams({ watch_id: watch, origin: route[0], destination: route[1] });
  const points = await api(`/api/trend?${params}`);
  $("trend-title").textContent = `${route[0]} → ${route[1]}`;
  if (!points.length) {
    chart.hidden = true;
    empty.hidden = false;
    empty.textContent = "No price history for this route yet.";
    $("trend-range").textContent = "";
    return;
  }
  chart.hidden = false;
  empty.hidden = true;
  drawTrend(chart, points);
  const prices = points.map((point) => Number(point.price));
  $("trend-range").textContent = `${money(Math.min(...prices), points[0].currency)} lowest · ${points.length} search${points.length === 1 ? "" : "es"}`;
}

function drawTrend(chart, points) {
  const namespace = "http://www.w3.org/2000/svg";
  const width = 800;
  const height = 180;
  const padding = { left: 80, right: 24, top: 18, bottom: 38 };
  const prices = points.map((point) => Number(point.price));
  const minimum = Math.min(...prices);
  const maximum = Math.max(...prices);
  const spread = Math.max(maximum - minimum, maximum * 0.05, 1);
  const low = Math.max(0, minimum - spread * 0.2);
  const high = maximum + spread * 0.2;
  const x = (index) => padding.left + (points.length === 1 ? (width - padding.left - padding.right) / 2 : index * (width - padding.left - padding.right) / (points.length - 1));
  const y = (price) => padding.top + (high - price) * (height - padding.top - padding.bottom) / (high - low);
  for (const fraction of [0, 0.5, 1]) {
    const gridY = padding.top + fraction * (height - padding.top - padding.bottom);
    const line = document.createElementNS(namespace, "line");
    line.setAttribute("x1", String(padding.left));
    line.setAttribute("x2", String(width - padding.right));
    line.setAttribute("y1", String(gridY));
    line.setAttribute("y2", String(gridY));
    line.setAttribute("class", "chart-grid");
    chart.append(line);
  }
  const coordinates = points.map((point, index) => `${x(index)},${y(Number(point.price))}`);
  const area = document.createElementNS(namespace, "path");
  area.setAttribute("d", `M ${padding.left},${height - padding.bottom} L ${coordinates.join(" L ")} L ${width - padding.right},${height - padding.bottom} Z`);
  area.setAttribute("class", "chart-area");
  chart.append(area);
  const line = document.createElementNS(namespace, "polyline");
  line.setAttribute("points", coordinates.join(" "));
  line.setAttribute("class", "chart-line");
  chart.append(line);
  points.forEach((point, index) => {
    const dot = document.createElementNS(namespace, "circle");
    dot.setAttribute("cx", String(x(index)));
    dot.setAttribute("cy", String(y(Number(point.price))));
    dot.setAttribute("r", "5");
    dot.setAttribute("class", "chart-dot");
    const title = document.createElementNS(namespace, "title");
    title.textContent = `${formatDate(point.observed_at)}: ${money(point.price, point.currency)}`;
    dot.append(title);
    chart.append(dot);
  });
  const labels = [
    { x: 2, y: padding.top + 7, text: money(maximum, points[0].currency) },
    { x: 2, y: height - padding.bottom + 7, text: money(minimum, points[0].currency) },
    { x: padding.left, y: height - 8, text: formatShortDate(points[0].observed_at) },
    { x: width - padding.right, y: height - 8, text: formatShortDate(points.at(-1).observed_at), anchor: "end" },
  ];
  labels.forEach((label) => {
    const textNode = document.createElementNS(namespace, "text");
    textNode.setAttribute("x", String(label.x));
    textNode.setAttribute("y", String(label.y));
    textNode.setAttribute("class", "chart-label");
    if (label.anchor) textNode.setAttribute("text-anchor", label.anchor);
    textNode.textContent = label.text;
    chart.append(textNode);
  });
}

function formatShortDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

async function loadDeliveryStatus() {
  const payload = await api("/api/delivery-status?limit=12");
  const delivered = payload.notifications.filter((item) => item.status === "success").length;
  const failed = payload.notifications.filter((item) => item.status !== "success").length;
  $("delivery-summary").textContent = `${delivered} delivered${failed ? ` · ${failed} failed` : ""}`;
  renderEvents("delivery-list", payload.notifications, (item) => ({
    title: `${readableName(item.provider)} · ${readableName(item.reason)}`,
    detail: `${item.watch_id === "__health__" ? "System event" : readableName(item.watch_id)} · ${formatDate(item.attempted_at)}`,
    status: item.status,
  }), "No notification attempts recorded yet.");
  renderEvents("failure-list", payload.source_failures, (item) => ({
    title: readableName(item.category),
    detail: `${item.watch_id ? readableName(item.watch_id) : "All watches"} · ${formatDate(item.occurred_at)}`,
    status: "failed",
  }), "No fare-source issues recorded.");
}

function renderEvents(targetId, items, describe, emptyText) {
  const list = $(targetId);
  list.replaceChildren();
  if (!items.length) {
    list.append(element("p", "empty-compact", emptyText));
    return;
  }
  items.slice(0, 5).forEach((item) => {
    const description = describe(item);
    const row = element("div", "event-row");
    row.append(element("strong", "", description.title));
    row.append(element("span", `event-status ${description.status}`, description.status));
    row.append(element("small", "", description.detail));
    list.append(row);
  });
}

async function refresh() {
  $("refresh-button").disabled = true;
  try {
    const [payload] = await Promise.all([api("/api/state"), loadAirports()]);
    renderState(payload);
    await Promise.all([loadHistory(), loadDeliveryStatus()]);
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
        updatePreview();
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
  $("exact-departure").required = mode === "exact";
  $("exact-return").required = mode === "exact" && $("trip-type").value === "round_trip";
  $("range-start").required = mode === "range";
  $("range-end").required = mode === "range";
  updatePreview();
}

function updatePreview() {
  const origins = codes($("origins").value);
  const destinations = codes($("destinations").value);
  const routeCount = origins.length * destinations.length;
  $("preview-route").textContent = routeCount
    ? `${origins.join(", ")} → ${destinations.join(", ")} · ${routeCount} route${routeCount === 1 ? "" : "s"}`
    : "Add origin and destination airports";
  const price = Number($("maximum-price").value);
  const currency = $("currency").value.trim().toUpperCase() || "your currency";
  const mode = $("date-mode").value;
  let timing = "your selected dates";
  if (mode === "exact" && $("exact-departure").value) {
    timing = $("trip-type").value === "round_trip" && $("exact-return").value
      ? `${$("exact-departure").value} to ${$("exact-return").value}`
      : $("exact-departure").value;
  } else if (mode === "range" && $("range-start").value && $("range-end").value) {
    timing = `${$("range-start").value} through ${$("range-end").value}`;
  } else if (mode === "rolling") {
    timing = `${$("rolling-start").value || 0}–${$("rolling-end").value || 0} days from now`;
  }
  const threshold = price > 0 ? money(price, currency) : "your price limit";
  const transfer = $("self-transfer").checked ? "self-transfers allowed" : "no self-transfers";
  $("preview-rule").textContent = `For ${timing}, alert below ${threshold}, with ${stopsDescription($("max-stops").value === "" ? null : Number($("max-stops").value))} and ${transfer}.`;
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
  updatePreview();
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
    toast(`${readableName(watch.id)} saved and ${watch.enabled ? "monitoring" : "paused"}.`);
    await refresh();
  } catch (error) { $("form-error").textContent = error.message; }
}

async function toggleWatch(watch) {
  try {
    await api(`/api/watches/${encodeURIComponent(watch.id)}/enabled`, { method: "POST", body: JSON.stringify({ enabled: !watch.enabled }) });
    toast(`${readableName(watch.id)} is now ${watch.enabled ? "paused" : "monitoring"}.`);
    await refresh();
  } catch (error) { toast(error.message); }
}

async function startCheck(watchId = null) {
  const scope = watchId ? readableName(watchId) : "all active watches";
  if (!window.confirm(`Search live fares for ${scope} now? If a fare matches every rule, AutoFly may send a notification.`)) return;
  const button = $("check-all-button");
  button.disabled = true;
  try {
    const job = await api("/api/checks", { method: "POST", body: JSON.stringify({ watch_id: watchId }) });
    $("check-feedback").textContent = "Searching fares… You can leave this page.";
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
        $("check-feedback").textContent = "Searching fares… You can leave this page.";
        window.setTimeout(poll, 2000);
        return;
      }
      $("check-feedback").textContent = "";
      $("check-all-button").disabled = false;
      state.activeJob = null;
      toast(job.status === "completed" ? "Fare search finished. Results are now up to date." : job.error || "Fare search failed");
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
$("history-watch").addEventListener("change", () => {
  updateRouteFilter();
  loadHistory();
});
$("history-route").addEventListener("change", loadHistory);
$("history-stops").addEventListener("change", loadHistory);
$("history-deals").addEventListener("change", loadHistory);
let airlineTimer;
$("history-airline").addEventListener("input", () => {
  window.clearTimeout(airlineTimer);
  airlineTimer = window.setTimeout(loadHistory, 250);
});
$("date-mode").addEventListener("change", (event) => setDateMode(event.target.value));
$("trip-type").addEventListener("change", () => {
  if ($("trip-type").value === "round_trip" && $("date-mode").value !== "exact") {
    toast("Round trips currently use exact departure and return dates.");
  }
  setDateMode($("date-mode").value);
});
$("watch-form").addEventListener("submit", saveWatch);
$("cancel-watch").addEventListener("click", () => $("watch-dialog").close());
$("watch-form").querySelectorAll("input, select").forEach((control) => {
  control.addEventListener("input", updatePreview);
  control.addEventListener("change", updatePreview);
});
initAirportAutocomplete("origins");
initAirportAutocomplete("destinations");

const sectionObserver = new IntersectionObserver((entries) => {
  const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
  if (!visible) return;
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.classList.toggle("active", link.getAttribute("href") === `#${visible.target.id}`);
  });
}, { rootMargin: "-20% 0px -65%", threshold: [0, 0.25, 0.5] });
["overview", "watches", "history", "activity"].forEach((id) => sectionObserver.observe($(id)));

refresh();
