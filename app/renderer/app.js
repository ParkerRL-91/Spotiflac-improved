// Spotiflac renderer logic (vanilla JS, no build step).
// Talks to the local Python sidecar over HTTP + WebSocket.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let API = ""; // http://127.0.0.1:<port>
let ws = null;
const jobs = new Map(); // uid -> job status + tracks

// ---- bootstrap ------------------------------------------------------------
async function boot() {
  const port = await window.spotiflac.getPort();
  API = `http://127.0.0.1:${port}`;
  connectWs(port);
  bindUi();
  await refreshJobs();
}

function connectWs(port) {
  setConn("connecting");
  ws = new WebSocket(`ws://127.0.0.1:${port}/ws`);
  ws.onopen = () => setConn("online");
  ws.onclose = () => {
    setConn("offline");
    setTimeout(() => connectWs(port), 1500); // auto-reconnect
  };
  ws.onerror = () => setConn("offline");
  ws.onmessage = (ev) => handleEvent(JSON.parse(ev.data));
}

function setConn(state) {
  const dot = $("#conn-dot");
  const text = $("#conn-text");
  dot.className = "status-dot " + (state === "online" ? "online" : state === "offline" ? "offline" : "");
  text.textContent = state === "online" ? "engine ready" : state === "offline" ? "reconnecting…" : "connecting…";
}

// ---- events from the sidecar ---------------------------------------------
function handleEvent(msg) {
  if (msg.type === "snapshot") {
    msg.jobs.forEach(upsertJob);
    renderJobs();
    return;
  }
  if (msg.job) {
    const entry = upsertJob(msg.job);
    if (msg.type === "track" && msg.track) {
      entry.current = msg.track;
    }
    renderJobs();
  }
}

function upsertJob(status) {
  let entry = jobs.get(status.uid);
  if (!entry) {
    entry = { status, current: null };
    jobs.set(status.uid, entry);
  } else {
    entry.status = status;
  }
  return entry;
}

// ---- API helpers ----------------------------------------------------------
async function api(method, path, body) {
  const res = await fetch(API + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function refreshJobs() {
  try {
    const list = await api("GET", "/api/jobs");
    list.forEach(upsertJob);
    renderJobs();
  } catch (_e) {
    /* sidecar may still be warming up */
  }
}

// ---- UI binding -----------------------------------------------------------
function bindUi() {
  $$(".nav-item").forEach((btn) =>
    btn.addEventListener("click", () => switchView(btn.dataset.view))
  );

  $("#choose-folder").addEventListener("click", async () => {
    const folder = await window.spotiflac.chooseFolder();
    if (folder) $("#folder").value = folder;
  });

  $("#start").addEventListener("click", startDownload);
  $("#profile-search").addEventListener("click", searchProfile);
  $("#profile-query").addEventListener("keydown", (e) => {
    if (e.key === "Enter") searchProfile();
  });

  // Backend + Tidal connect
  $("#backend").addEventListener("change", toggleTidalCard);
  $("#tidal-connect").addEventListener("click", connectTidal);
  toggleTidalCard();

  // Library
  $("#lib-choose-folder").addEventListener("click", async () => {
    const folder = await window.spotiflac.chooseFolder();
    if (folder) $("#lib-folder").value = folder;
  });
  $("#lib-start").addEventListener("click", startLibraryDownload);
}

// ---- Tidal connect --------------------------------------------------------
function toggleTidalCard() {
  const isTidal = $("#backend").value === "tidal";
  $("#tidal-card").hidden = !isTidal;
  if (isTidal) refreshTidalStatus();
}

async function refreshTidalStatus() {
  try {
    const s = await api("GET", "/api/tidal/status");
    const dot = $("#tidal-dot");
    const text = $("#tidal-text");
    const hint = $("#tidal-hint");
    if (s.linked) {
      dot.className = "status-dot online";
      text.textContent = "Tidal connected";
      $("#tidal-connect").hidden = true;
      hint.hidden = true;
    } else if (s.state === "awaiting_user" && s.url) {
      dot.className = "status-dot";
      text.textContent = "Waiting for you to approve in browser…";
      hint.hidden = false;
      hint.innerHTML = `Open <a href="${s.url}" target="_blank" rel="noreferrer">${escapeHtml(s.url)}</a> and approve, then this updates automatically.`;
      setTimeout(refreshTidalStatus, 3000);
    } else {
      dot.className = "status-dot offline";
      text.textContent = "Tidal not connected";
      $("#tidal-connect").hidden = false;
    }
  } catch (_e) {
    /* ignore */
  }
}

async function connectTidal() {
  try {
    const s = await api("POST", "/api/tidal/login");
    if (s.url) {
      window.spotiflac.openPath(s.url); // open in default browser
    }
    toast("Approve the Tidal login in your browser.");
    refreshTidalStatus();
  } catch (err) {
    toast(`Could not start Tidal login: ${err.message}`, true);
  }
}

// ---- Library --------------------------------------------------------------
async function startLibraryDownload() {
  const folder = $("#lib-folder").value.trim();
  const backend = $("#lib-backend").value;
  if (backend === "tidal") {
    const s = await api("GET", "/api/tidal/status").catch(() => ({}));
    if (!s.linked) {
      toast("Connect your Tidal account first (Download tab → Tidal).", true);
      switchView("download");
      $("#backend").value = "tidal";
      toggleTidalCard();
      return;
    }
  }
  const payload = {
    liked: $("#lib-liked").checked,
    albums: $("#lib-albums").checked,
    playlists: $("#lib-playlists").checked,
    followed_artists: $("#lib-artists").checked,
    backend,
    format: backend === "tidal" ? "flac" : "mp3",
    output: folder ? buildOutput("lib-folder", "lib-organize") : undefined,
  };
  const btn = $("#lib-start");
  btn.disabled = true;
  try {
    await api("POST", "/api/jobs/library", payload);
    toast("Library download started — opening a browser to sign in to Spotify if needed.");
    switchView("activity");
  } catch (err) {
    toast(`Could not start: ${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
}

function switchView(view) {
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
}

// ---- download -------------------------------------------------------------
// Map an "organize" choice to a spotdl output template. (For the Tidal backend
// streamrip applies its own artist/album layout under the chosen root folder.)
function organizeTemplate(mode) {
  switch (mode) {
    case "artist-album-track":
      return "{artist}/{album}/{track-number} - {title}.{output-ext}";
    case "artist-track":
      return "{artist}/{artist} - {title}.{output-ext}";
    case "album-track":
      return "{album}/{track-number} - {title}.{output-ext}";
    default:
      return "{artists} - {title}.{output-ext}";
  }
}

function buildOutput(folderId = "folder", organizeId = "organize") {
  const folder = $("#" + folderId).value.trim();
  const template = organizeTemplate($("#" + organizeId).value);
  return folder ? `${folder}/${template}` : template;
}

async function startDownload() {
  const raw = $("#query").value.trim();
  const query = raw
    .split("\n")
    .map((q) => q.trim())
    .filter(Boolean);
  if (query.length === 0) {
    toast("Add at least one link or search query.", true);
    return;
  }
  const bitrate = $("#bitrate").value;
  const backend = $("#backend").value;
  const payload = {
    query,
    output: buildOutput(),
    format: backend === "tidal" ? "flac" : $("#format").value,
    bitrate: bitrate === "auto" ? "auto" : bitrate,
    threads: parseInt($("#threads").value, 10) || undefined,
    overwrite: $("#overwrite").value,
    batch_size: parseInt($("#batch").value, 10) || 50,
    max_track_attempts: parseInt($("#attempts").value, 10) || 4,
    backend,
  };
  const btn = $("#start");
  btn.disabled = true;
  try {
    await api("POST", "/api/jobs", payload);
    toast("Download started.");
    switchView("activity");
  } catch (err) {
    toast(`Could not start: ${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
}

async function queueDownload(url, label) {
  try {
    await api("POST", "/api/jobs", {
      query: [url],
      output: buildOutput(),
      format: $("#format").value,
      bitrate: $("#bitrate").value,
      overwrite: $("#overwrite").value,
    });
    toast(`Queued: ${label}`);
    switchView("activity");
  } catch (err) {
    toast(`Could not queue: ${err.message}`, true);
  }
}

// ---- jobs rendering -------------------------------------------------------
function renderJobs() {
  const container = $("#jobs");
  const active = Array.from(jobs.values()).filter(
    (e) => !["completed", "cancelled", "failed"].includes(e.status.state)
  ).length;
  const badge = $("#activity-badge");
  badge.hidden = active === 0;
  badge.textContent = String(active);

  $("#jobs-empty").style.display = jobs.size === 0 ? "block" : "none";

  container.innerHTML = "";
  const sorted = Array.from(jobs.values()).reverse();
  for (const entry of sorted) {
    container.appendChild(renderJob(entry));
  }
}

function renderJob(entry) {
  const s = entry.status;
  const c = s.counts || {};
  const total = c.total || 0;
  const done = (c.completed || 0) + (c.skipped || 0);
  const pct = total ? Math.round(((done + (c.failed || 0)) / total) * 100) : 0;

  const el = document.createElement("div");
  el.className = "job";
  el.innerHTML = `
    <div class="job-head">
      <span class="job-title">${escapeHtml(jobLabel(s))}</span>
      <span class="job-state ${s.state}">${s.state}</span>
    </div>
    <div class="bar"><i style="width:${pct}%"></i></div>
    <div class="job-meta">
      <span><b>${done}</b> done</span>
      <span><b>${c.failed || 0}</b> failed</span>
      <span><b>${c.skipped || 0}</b> skipped</span>
      <span><b>${total}</b> total</span>
      ${s.rate_limit_delay ? `<span>throttle <b>${s.rate_limit_delay}s</b></span>` : ""}
    </div>
    <div class="job-current">${entry.current ? escapeHtml(`${entry.current.message} — ${entry.current.name}`) : ""}</div>
    <div class="job-actions"></div>
  `;

  const actions = el.querySelector(".job-actions");
  if (["downloading", "searching", "queued"].includes(s.state)) {
    actions.appendChild(actionBtn("Pause", "ghost small", () => control(s.uid, "pause")));
    actions.appendChild(actionBtn("Cancel", "danger small", () => control(s.uid, "cancel")));
  } else if (s.state === "paused") {
    actions.appendChild(actionBtn("Resume", "primary small", () => control(s.uid, "resume")));
    actions.appendChild(actionBtn("Cancel", "danger small", () => control(s.uid, "cancel")));
  }
  if (s.error) {
    const err = document.createElement("div");
    err.className = "hint";
    err.style.color = "var(--danger)";
    err.textContent = s.error;
    el.appendChild(err);
  }
  return el;
}

function jobLabel(s) {
  if (s.query && s.query.length === 1) return s.query[0];
  return `${s.query ? s.query.length : 0} items`;
}

function actionBtn(label, cls, onClick) {
  const b = document.createElement("button");
  b.className = "btn " + cls;
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

async function control(uid, action) {
  try {
    await api("POST", `/api/jobs/${uid}/${action}`);
  } catch (err) {
    toast(err.message, true);
  }
}

// ---- profiles -------------------------------------------------------------
async function searchProfile() {
  const q = $("#profile-query").value.trim();
  if (!q) return;
  const result = $("#profile-result");
  result.innerHTML = `<div class="empty">Looking up…</div>`;
  try {
    const profile = await api("GET", `/api/profile?user=${encodeURIComponent(q)}`);
    renderProfile(profile);
  } catch (err) {
    result.innerHTML = `<div class="card"><p class="hint" style="color:var(--danger)">${escapeHtml(err.message)}</p></div>`;
  }
}

function renderProfile(p) {
  const result = $("#profile-result");
  const avatar = p.image
    ? `<img src="${p.image}" alt="" />`
    : `<div class="profile-avatar">${escapeHtml((p.display_name || "?")[0])}</div>`;
  const followers = p.followers != null ? `${p.followers.toLocaleString()} followers` : "";

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="profile-card">
      ${avatar}
      <div>
        <h2 style="margin:0">${escapeHtml(p.display_name)}</h2>
        <div class="hint">${escapeHtml(followers)} · ${p.playlists.length} public playlist(s)</div>
      </div>
    </div>
  `;
  result.innerHTML = "";
  result.appendChild(card);

  if (p.playlists.length === 0) {
    const none = document.createElement("div");
    none.className = "empty";
    none.textContent = "No public playlists found for this user.";
    result.appendChild(none);
    return;
  }

  for (const pl of p.playlists) {
    const row = document.createElement("div");
    row.className = "playlist";
    const img = pl.image ? `<img src="${pl.image}" alt="" />` : `<div class="ph"></div>`;
    row.innerHTML = `
      ${img}
      <div class="pl-info">
        <strong>${escapeHtml(pl.name || "Untitled")}</strong>
        <small>${pl.tracks != null ? pl.tracks + " tracks" : ""}${pl.owner ? " · " + escapeHtml(pl.owner) : ""}</small>
      </div>
    `;
    const dl = actionBtn("Download", "primary small", () => queueDownload(pl.url, pl.name));
    dl.disabled = !pl.url;
    row.appendChild(dl);
    result.appendChild(row);
  }
}

// ---- utils ----------------------------------------------------------------
let toastTimer = null;
function toast(message, isError) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3500);
}

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

boot();
