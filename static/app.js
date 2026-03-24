// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let TOKEN = localStorage.getItem("token") || "";
let activeRouteId = null;

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------
async function api(method, path, body) {
  const opts = {
    method,
    headers: { Authorization: `Bearer ${TOKEN}` },
  };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Token setup
// ---------------------------------------------------------------------------
function showTokenForm() {
  document.getElementById("token-screen").hidden = false;
  document.getElementById("main-app").hidden = true;
}

function showApp() {
  document.getElementById("token-screen").hidden = true;
  document.getElementById("main-app").hidden = false;
}

document.getElementById("token-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const val = document.getElementById("token-input").value.trim();
  if (!val) return;
  TOKEN = val;
  localStorage.setItem("token", TOKEN);
  await boot();
});

document.getElementById("logout-btn").addEventListener("click", () => {
  localStorage.removeItem("token");
  TOKEN = "";
  showTokenForm();
});

// ---------------------------------------------------------------------------
// Status panel
// ---------------------------------------------------------------------------
async function refreshStatus() {
  const el = document.getElementById("status-panel");
  try {
    const data = await api("GET", "/api/status");
    activeRouteId = data.route_id;
    const qt = new Date(data.query_time);
    const age = Math.round((Date.now() - qt) / 60000);
    el.innerHTML = `
      <div class="stat-grid">
        <div class="stat">
          <span class="stat-label">Route</span>
          <span class="stat-value">${data.route_name}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Travel time</span>
          <span class="stat-value">${data.traffic_duration_mins.toFixed(1)} min</span>
        </div>
        <div class="stat">
          <span class="stat-label">Delay</span>
          <span class="stat-value ${data.delay_mins > 5 ? "bad" : data.delay_mins > 2 ? "warn" : "good"}">${data.delay_mins > 0 ? "+" : ""}${data.delay_mins} min</span>
        </div>
        <div class="stat">
          <span class="stat-label">Updated</span>
          <span class="stat-value">${age === 0 ? "just now" : `${age}m ago`}</span>
        </div>
      </div>`;
    refreshChart();
  } catch (err) {
    el.innerHTML = `<p class="error">Could not load status: ${err.message}</p>`;
  }
}

function refreshChart() {
  if (!activeRouteId) return;
  const img = document.getElementById("chart-img");
  img.src = `/api/chart/${activeRouteId}?token=${encodeURIComponent(TOKEN)}&t=${Date.now()}`;
  img.hidden = false;
}

// ---------------------------------------------------------------------------
// Routes panel
// ---------------------------------------------------------------------------
async function loadRoutes() {
  const list = document.getElementById("routes-list");
  try {
    const data = await api("GET", "/api/routes");
    list.innerHTML = data.routes.map((r) => `
      <div class="route-card ${r.active ? "active" : ""}">
        <div class="route-info">
          <strong>${r.name}</strong>
          <small>${r.origin} → ${r.destination}</small>
          <small>Arrive by ${r.arrival_time} · ${r.provider}</small>
        </div>
        <div class="route-actions">
          ${!r.active ? `<button onclick="activateRoute('${r.id}')">Activate</button>` : '<span class="badge">Active</span>'}
          <button onclick="editRoute(${JSON.stringify(JSON.stringify(r))})">Edit</button>
          <button class="danger" onclick="deleteRoute('${r.id}', '${r.name}')">Delete</button>
        </div>
      </div>`).join("") || "<p>No routes saved yet.</p>";
  } catch (err) {
    list.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

window.activateRoute = async (id) => {
  await api("POST", `/api/routes/${id}/activate`);
  await loadRoutes();
  await refreshStatus();
};

window.deleteRoute = async (id, name) => {
  if (!confirm(`Delete "${name}"?`)) return;
  await api("DELETE", `/api/routes/${id}`);
  await loadRoutes();
};

window.editRoute = (jsonStr) => {
  const r = JSON.parse(jsonStr);
  document.getElementById("route-id").value = r.id;
  document.getElementById("route-name").value = r.name;
  document.getElementById("route-origin").value = r.origin;
  document.getElementById("route-destination").value = r.destination;
  document.getElementById("route-arrival").value = r.arrival_time;
  document.getElementById("route-timezone").value = r.timezone;
  document.getElementById("route-provider").value = r.provider;
  document.getElementById("route-id").readOnly = true;
  document.getElementById("route-form-title").textContent = "Edit Route";
  document.getElementById("route-form-section").hidden = false;
  document.getElementById("route-form-section").scrollIntoView({ behavior: "smooth" });
};

document.getElementById("add-route-btn").addEventListener("click", () => {
  document.getElementById("route-form").reset();
  document.getElementById("route-id").readOnly = false;
  document.getElementById("route-form-title").textContent = "Add Route";
  document.getElementById("route-form-section").hidden = false;
});

document.getElementById("cancel-route-btn").addEventListener("click", () => {
  document.getElementById("route-form-section").hidden = true;
});

document.getElementById("route-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("route-id").value.trim();
  const body = {
    id,
    name: document.getElementById("route-name").value.trim(),
    origin: document.getElementById("route-origin").value.trim(),
    destination: document.getElementById("route-destination").value.trim(),
    arrival_time: document.getElementById("route-arrival").value,
    timezone: document.getElementById("route-timezone").value.trim(),
    provider: document.getElementById("route-provider").value,
  };
  const isEdit = document.getElementById("route-id").readOnly;
  try {
    if (isEdit) {
      await api("PUT", `/api/routes/${id}`, body);
    } else {
      await api("POST", "/api/routes", body);
    }
    document.getElementById("route-form-section").hidden = true;
    await loadRoutes();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
});

// ---------------------------------------------------------------------------
// Push notifications
// ---------------------------------------------------------------------------
async function setupPush() {
  const btn = document.getElementById("push-btn");
  const status = document.getElementById("push-status");

  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    status.textContent = "Push notifications not supported in this browser.";
    btn.disabled = true;
    return;
  }

  const reg = await navigator.serviceWorker.ready;
  const existing = await reg.pushManager.getSubscription();

  if (existing) {
    status.textContent = "Notifications enabled.";
    btn.textContent = "Disable notifications";
    btn.onclick = async () => {
      await existing.unsubscribe();
      await api("DELETE", "/api/push/subscribe");
      status.textContent = "Notifications disabled.";
      btn.textContent = "Enable notifications";
      btn.onclick = enablePush;
    };
  } else {
    btn.textContent = "Enable notifications";
    btn.onclick = enablePush;
    status.textContent = "";
  }

  async function enablePush() {
    try {
      const { public_key } = await api("GET", "/api/push/public-key");
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(public_key),
      });
      await api("POST", "/api/push/subscribe", {
        endpoint: sub.endpoint,
        keys: {
          p256dh: arrayBufferToBase64(sub.getKey("p256dh")),
          auth: arrayBufferToBase64(sub.getKey("auth")),
        },
      });
      status.textContent = "Notifications enabled.";
      btn.textContent = "Disable notifications";
      btn.onclick = async () => {
        await sub.unsubscribe();
        await api("DELETE", "/api/push/subscribe");
        status.textContent = "Notifications disabled.";
        btn.textContent = "Enable notifications";
        btn.onclick = enablePush;
      };
    } catch (err) {
      status.textContent = `Error: ${err.message}`;
    }
  }
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

function arrayBufferToBase64(buffer) {
  return btoa(String.fromCharCode(...new Uint8Array(buffer)));
}

document.getElementById("refresh-btn").addEventListener("click", refreshStatus);

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => (p.hidden = true));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).hidden = false;
    if (btn.dataset.tab === "routes") loadRoutes();
  });
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
async function boot() {
  try {
    showApp();
    await refreshStatus();

    if ("serviceWorker" in navigator) {
      await navigator.serviceWorker.register("/sw.js");
      await setupPush();
    }

    // Refresh status every 5 minutes
    setInterval(refreshStatus, 5 * 60 * 1000);
  } catch (err) {
    if (err.message.includes("401") || err.message.includes("Invalid token") || err.message.includes("Unauthorized")) {
      localStorage.removeItem("token");
      TOKEN = "";
      showTokenForm();
    } else {
      document.getElementById("status-panel").innerHTML = `<p class="error">${err.message}</p>`;
      showApp();
    }
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
if (TOKEN) {
  boot();
} else {
  showTokenForm();
}
