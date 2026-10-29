// ---------------------------------------------------------------------------
// State & helpers
// ---------------------------------------------------------------------------
let TOKEN = localStorage.getItem("token") || "";
let lastData = null;
let lastFetch = 0;

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const LEVEL_LABELS = { green: "Normal", orange: "Slower than usual", red: "Significant delays" };
const CHART_START = 6 * 60;
const CHART_END = 22 * 60;
const STALE_AFTER_MINS = 15;

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

const hhmm = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const clock = (mins) => `${String(Math.floor(mins / 60)).padStart(2, "0")}:${String(mins % 60).padStart(2, "0")}`;
const round = (value) => Math.round(value);

function dayLabel(iso) {
  const date = new Date(iso);
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === tomorrow.toDateString()) return "Tomorrow";
  return date.toLocaleDateString([], { weekday: "long" });
}

async function api(method, path, body) {
  const opts = { method, headers: { Authorization: `Bearer ${TOKEN}` } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const error = new Error(err.detail || res.statusText);
    error.status = res.status;
    throw error;
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Sign in / out
// ---------------------------------------------------------------------------
function showTokenForm() {
  $("token-screen").hidden = false;
  $("main-app").hidden = true;
}

function signOut() {
  localStorage.removeItem("token");
  TOKEN = "";
  lastData = null;
  showTokenForm();
}

$("token-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const value = $("token-input").value.trim();
  if (!value) return;
  TOKEN = value;
  localStorage.setItem("token", TOKEN);
  boot();
});

$("logout-btn").addEventListener("click", signOut);

// ---------------------------------------------------------------------------
// Now view
// ---------------------------------------------------------------------------
async function refresh() {
  try {
    const data = await api("GET", "/api/summary");
    lastData = data;
    lastFetch = Date.now();
    $("error").hidden = true;
    render(data);
  } catch (err) {
    if (err.status === 401 || err.status === 403) return signOut();
    $("error").textContent = `Could not load: ${err.message}`;
    $("error").hidden = false;
  }
}

function render(data) {
  $("route-name").textContent = data.route_name;
  renderVerdict(data);
  renderChart(data.chart);
  renderUpdated(data);
}

function renderVerdict(data) {
  const { now, departure } = data;
  const leaveBy = hhmm(departure.leave_by);
  const next = $("verdict-next");
  let level;
  if (departure.active) {
    level = departure.level;
    const late = Date.now() >= new Date(departure.leave_by).getTime();
    $("verdict-headline").textContent = late ? "Leave now" : `Leave by ${leaveBy}`;
    $("verdict-detail").textContent =
      `${round(departure.mins)} min · typical ${round(departure.typical_mins)} · arrive ${hhmm(departure.arrive_by)}` +
      (level === "red" ? " · check Waze" : "");
    next.hidden = true;
  } else {
    level = now.level;
    $("verdict-headline").textContent = `${round(now.mins)} min now`;
    $("verdict-detail").textContent = `Typical ${round(now.typical_mins)} min at this time`;
    next.replaceChildren(
      document.createTextNode(`${dayLabel(departure.arrive_by)}'s run: leave around `),
      el("strong", {}, leaveBy),
      document.createTextNode(` to arrive ${hhmm(departure.arrive_by)}`),
    );
    next.hidden = false;
  }
  $("verdict").dataset.level = level;
  $("verdict-label").textContent = LEVEL_LABELS[level];
}

function renderUpdated(data) {
  const age = Math.max(0, Math.round((Date.now() - new Date(data.sample_time)) / 60000));
  const stale = age > STALE_AFTER_MINS;
  $("updated").textContent = stale
    ? `Last reading ${age} min ago — the monitor may be down`
    : age === 0 ? "Updated just now" : `Updated ${age} min ago`;
  $("updated-row").classList.toggle("stale", stale);
}

$("refresh-btn").addEventListener("click", refresh);

// ---------------------------------------------------------------------------
// Chart: today's line against the typical line and usual range
// ---------------------------------------------------------------------------
// Each bucket's typical/range comes from ~5 readings, so one slow day makes a spike. Smooth the
// reference series over 15 minutes for display; the verdict card uses the exact values.
function smooth(values, radius = 1) {
  return values.map((value, i) => {
    if (value == null) return null;
    const window = values.slice(Math.max(0, i - radius), i + radius + 1).filter((v) => v != null);
    return window.reduce((sum, v) => sum + v, 0) / window.length;
  });
}

function chartPoints(chart) {
  const typical = smooth(chart.typical);
  const low = smooth(chart.low);
  const high = smooth(chart.high);
  const points = [];
  chart.minutes.forEach((minute, i) => {
    if (minute < CHART_START || minute >= CHART_END) return;
    points.push({ minute, today: chart.today[i], typical: typical[i], low: low[i], high: high[i] });
  });
  return points;
}

function niceStep(span) {
  return [1, 2, 5, 10].find((step) => span / step <= 4) || 20;
}

function linePath(points, key, x, y) {
  let d = "";
  let pen = false;
  for (const p of points) {
    if (p[key] == null) { pen = false; continue; }
    d += `${pen ? "L" : "M"}${x(p.minute).toFixed(1)},${y(p[key]).toFixed(1)}`;
    pen = true;
  }
  return d;
}

function bandPath(points, x, y) {
  // One closed polygon per unbroken run of buckets that have a range.
  const runs = [[]];
  for (const p of points) {
    if (p.low == null || p.high == null) { if (runs.at(-1).length) runs.push([]); continue; }
    runs.at(-1).push(p);
  }
  return runs.filter((run) => run.length > 1).map((run) => {
    const upper = run.map((p) => `${x(p.minute).toFixed(1)},${y(p.high).toFixed(1)}`);
    const lower = run.slice().reverse().map((p) => `${x(p.minute).toFixed(1)},${y(p.low).toFixed(1)}`);
    return `M${upper.join("L")}L${lower.join("L")}Z`;
  }).join("");
}

function renderChart(chart) {
  const root = $("chart-svg");
  const width = root.clientWidth || 340;
  const height = 210;
  const margin = { top: 10, right: 10, bottom: 22, left: 30 };
  const points = chartPoints(chart);
  const values = points.flatMap((p) => [p.today, p.typical, p.low, p.high]).filter((v) => v != null);
  root.replaceChildren();
  root.setAttribute("viewBox", `0 0 ${width} ${height}`);
  if (!values.length) return;

  const step = niceStep(Math.max(...values) - Math.min(...values));
  const yMin = Math.floor(Math.min(...values) / step) * step;
  const yMax = Math.ceil(Math.max(...values) / step) * step;
  const x = (minute) => margin.left + ((minute - CHART_START) / (CHART_END - CHART_START)) * (width - margin.left - margin.right);
  const y = (value) => margin.top + (1 - (value - yMin) / (yMax - yMin || 1)) * (height - margin.top - margin.bottom);

  for (let v = yMin; v <= yMax; v += step) {
    root.append(svg("line", { x1: margin.left, x2: width - margin.right, y1: y(v), y2: y(v), stroke: "var(--grid)" }));
    const label = svg("text", { x: margin.left - 6, y: y(v) + 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 11 });
    label.textContent = `${v}m`;
    root.append(label);
  }
  for (let m = CHART_START; m < CHART_END; m += 180) {
    const label = svg("text", { x: x(m), y: height - 5, "text-anchor": "middle", fill: "var(--muted)", "font-size": 11 });
    label.textContent = clock(m);
    root.append(label);
  }

  root.append(svg("path", { d: bandPath(points, x, y), fill: "var(--band)" }));
  root.append(svg("path", {
    d: linePath(points, "typical", x, y), fill: "none", stroke: "var(--series-typical)",
    "stroke-width": 1.5, "stroke-dasharray": "4 3", "stroke-linejoin": "round",
  }));
  root.append(svg("path", {
    d: linePath(points, "today", x, y), fill: "none", stroke: "var(--series-today)",
    "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
  }));

  const nowDate = new Date();
  const nowMinute = nowDate.getHours() * 60 + nowDate.getMinutes();
  if (nowMinute >= CHART_START && nowMinute < CHART_END) {
    root.append(svg("line", {
      x1: x(nowMinute), x2: x(nowMinute), y1: margin.top, y2: height - margin.bottom,
      stroke: "var(--muted)", "stroke-width": 1, "stroke-dasharray": "2 3",
    }));
  }
  const latest = points.filter((p) => p.today != null).at(-1);
  if (latest) {
    root.append(svg("circle", {
      cx: x(latest.minute), cy: y(latest.today), r: 4,
      fill: "var(--series-today)", stroke: "var(--surface)", "stroke-width": 2,
    }));
  }

  // Crosshair layer
  const cross = svg("line", { y1: margin.top, y2: height - margin.bottom, stroke: "var(--muted)", "stroke-width": 1, visibility: "hidden" });
  const dotToday = svg("circle", { r: 4, fill: "var(--series-today)", stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" });
  const dotTypical = svg("circle", { r: 3.5, fill: "var(--series-typical)", stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" });
  root.append(cross, dotTypical, dotToday);
  root.chartState = { points, x, y, width, margin, cross, dotToday, dotTypical };

  renderTable(points);
}

function showCrosshair(clientX) {
  const state = $("chart-svg").chartState;
  if (!state) return;
  const rect = $("chart-svg").getBoundingClientRect();
  const px = ((clientX - rect.left) / rect.width) * state.width;
  const nearest = state.points.reduce((best, p) =>
    p.typical == null && p.today == null ? best : (!best || Math.abs(state.x(p.minute) - px) < Math.abs(state.x(best.minute) - px) ? p : best), null);
  if (!nearest) return;

  const cx = state.x(nearest.minute);
  state.cross.setAttribute("x1", cx);
  state.cross.setAttribute("x2", cx);
  state.cross.setAttribute("visibility", "visible");
  for (const [dot, key] of [[state.dotToday, "today"], [state.dotTypical, "typical"]]) {
    if (nearest[key] == null) { dot.setAttribute("visibility", "hidden"); continue; }
    dot.setAttribute("cx", cx);
    dot.setAttribute("cy", state.y(nearest[key]));
    dot.setAttribute("visibility", "visible");
  }

  const tip = $("tooltip");
  const rows = [el("div", { class: "t-time" }, clock(nearest.minute))];
  const row = (keyClass, value, label) => {
    const line = el("div", { class: "t-row" });
    line.append(el("i", { class: `key ${keyClass}` }), el("strong", {}, value), document.createTextNode(` ${label}`));
    return line;
  };
  if (nearest.today != null) rows.push(row("today", `${nearest.today.toFixed(1)} min`, "today"));
  if (nearest.typical != null) rows.push(row("typical", `${nearest.typical.toFixed(1)} min`, "typical"));
  if (nearest.low != null) rows.push(row("band", `${round(nearest.low)}–${round(nearest.high)} min`, "usual"));
  tip.replaceChildren(...rows);
  tip.hidden = false;
  const left = (cx / state.width) * rect.width;
  const tipWidth = tip.offsetWidth;
  tip.style.left = `${Math.min(Math.max(left - tipWidth / 2, 0), rect.width - tipWidth)}px`;
  tip.style.top = `-${tip.offsetHeight + 6}px`;
}

function hideCrosshair() {
  const state = $("chart-svg").chartState;
  if (!state) return;
  for (const node of [state.cross, state.dotToday, state.dotTypical]) node.setAttribute("visibility", "hidden");
  $("tooltip").hidden = true;
}

const chartWrap = $("chart");
chartWrap.addEventListener("pointerdown", (e) => showCrosshair(e.clientX));
chartWrap.addEventListener("pointermove", (e) => showCrosshair(e.clientX));
chartWrap.addEventListener("pointerleave", hideCrosshair);
chartWrap.addEventListener("pointercancel", hideCrosshair);

function renderTable(points) {
  const table = $("chart-table");
  const head = el("tr");
  for (const title of ["Time", "Today", "Typical", "Usual range"]) head.append(el("th", {}, title));
  const rows = points
    .filter((p) => p.minute % 30 === 0 && (p.today != null || p.typical != null))
    .map((p) => {
      const tr = el("tr");
      tr.append(
        el("td", {}, clock(p.minute)),
        el("td", {}, p.today != null ? p.today.toFixed(1) : "–"),
        el("td", {}, p.typical != null ? p.typical.toFixed(1) : "–"),
        el("td", {}, p.low != null ? `${round(p.low)}–${round(p.high)}` : "–"),
      );
      return tr;
    });
  table.replaceChildren(el("thead"), el("tbody"));
  table.tHead.append(head);
  table.tBodies[0].append(...rows);
}

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => lastData && renderChart(lastData.chart), 150);
});

// ---------------------------------------------------------------------------
// Settings view: routes
// ---------------------------------------------------------------------------
function showView(name) {
  $("view-now").hidden = name !== "now";
  $("view-settings").hidden = name !== "settings";
  if (name === "settings") loadRoutes();
  window.scrollTo(0, 0);
}

$("settings-btn").addEventListener("click", () => showView("settings"));
$("back-btn").addEventListener("click", () => { showView("now"); refresh(); });

async function loadRoutes() {
  const list = $("routes-list");
  try {
    const { routes } = await api("GET", "/api/routes");
    if (!routes.length) return list.replaceChildren(el("p", { class: "muted" }, "No routes yet."));
    list.replaceChildren(...routes.map(routeRow));
  } catch (err) {
    list.replaceChildren(el("p", { class: "error" }, err.message));
  }
}

function routeRow(route) {
  const row = el("div", { class: "route" });
  const name = el("strong", {}, route.name);
  if (route.active) name.append(el("span", { class: "badge" }, "ACTIVE"));
  const actions = el("div", { class: "route-actions" });
  if (!route.active) {
    const activate = el("button", {}, "Make active");
    activate.onclick = async () => { await api("POST", `/api/routes/${encodeURIComponent(route.id)}/activate`); loadRoutes(); };
    actions.append(activate);
  }
  const edit = el("button", { class: "secondary" }, "Edit");
  edit.onclick = () => openRouteForm(route);
  const remove = el("button", { class: "danger" }, "Delete");
  remove.onclick = async () => {
    if (!confirm(`Delete "${route.name}"?`)) return;
    await api("DELETE", `/api/routes/${encodeURIComponent(route.id)}`);
    loadRoutes();
  };
  actions.append(edit, remove);
  row.append(
    name,
    el("small", {}, `${route.origin} → ${route.destination}`),
    el("small", {}, `Arrive by ${route.arrival_time}`),
    actions,
  );
  return row;
}

function openRouteForm(route) {
  const form = $("route-form");
  form.reset();
  $("route-id").readOnly = Boolean(route);
  if (route) {
    $("route-id").value = route.id;
    $("route-name-input").value = route.name;
    $("route-origin").value = route.origin;
    $("route-destination").value = route.destination;
    $("route-arrival").value = route.arrival_time;
    $("route-timezone").value = route.timezone;
  }
  form.hidden = false;
  form.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("add-route-btn").addEventListener("click", () => openRouteForm(null));
$("cancel-route-btn").addEventListener("click", () => { $("route-form").hidden = true; });

$("route-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("route-id").value.trim();
  const body = {
    id,
    name: $("route-name-input").value.trim(),
    origin: $("route-origin").value.trim(),
    destination: $("route-destination").value.trim(),
    arrival_time: $("route-arrival").value,
    timezone: $("route-timezone").value.trim(),
  };
  try {
    if ($("route-id").readOnly) await api("PUT", `/api/routes/${encodeURIComponent(id)}`, body);
    else await api("POST", "/api/routes", body);
    $("route-form").hidden = true;
    loadRoutes();
  } catch (err) {
    alert(`Could not save: ${err.message}`);
  }
});

// ---------------------------------------------------------------------------
// Boot: refresh on open, on return to the app, and every 5 minutes while visible
// ---------------------------------------------------------------------------
let started = false;

function boot() {
  $("token-screen").hidden = true;
  $("main-app").hidden = false;
  showView("now");
  refresh();
  if (started) return;
  started = true;
  setInterval(() => {
    if (!lastData || document.hidden) return;
    renderUpdated(lastData);
    if (Date.now() - lastFetch > 5 * 60 * 1000) refresh();
  }, 30 * 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && TOKEN && Date.now() - lastFetch > 60 * 1000) refresh();
  });
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");

if (TOKEN) boot();
else showTokenForm();
