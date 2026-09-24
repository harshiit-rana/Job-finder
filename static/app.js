/* RoleRadar frontend — zero-dependency SPA
   search · facets · full-JD drawer · application tracker (localStorage) */
(() => {
"use strict";

/* ---------------- state ---------------- */
const state = {
  q: "", type: [], remote: [], geo: [], level: [], sources: [],
  posted: "", min_salary: 0, has_salary: false, include_expired: false,
  sort: "newest", page: 1, tag: "",
};
let sourceLabels = {};   // name -> label
let lastFacets = {}, lastTotal = 0, loading = false, reachedEnd = false;
const jobCache = new Map();   // fingerprint -> job preview

const $ = (id) => document.getElementById(id);
const els = {
  q: $("q"), sortSel: $("sortSel"), cards: $("cards"), loadMore: $("loadMore"),
  resultMeta: $("resultMeta"), activeFilters: $("activeFilters"),
  statChips: $("statChips"), syncDot: $("syncDot"), syncLabel: $("syncLabel"),
  sourcesStrip: $("sourcesStrip"), toast: $("toast"),
  drawer: $("drawer"), drawerOverlay: $("drawerOverlay"),
  drawerHead: $("drawerHead"), drawerBody: $("drawerBody"),
  tracker: $("tracker"), trackerOverlay: $("trackerOverlay"),
  trackerBoard: $("trackerBoard"), trackerSub: $("trackerSub"),
  trackerBtn: $("trackerBtn"), savedCount: $("savedCount"),
  fab: $("fabFilters"), filters: $("filters"),
};

const TYPE_META = {
  fulltime: ["Full-time", "b-fulltime"], parttime: ["Part-time", "b-parttime"],
  contract: ["Contract", "b-contract"], internship: ["Internship", "b-internship"],
  freelance: ["Freelance", "b-freelance"], apprenticeship: ["Apprenticeship", "b-apprenticeship"],
  research: ["Research", "b-research"], gig: ["Gig", "b-gig"], other: ["Other", "b-other"],
};
const REMOTE_META = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site", unknown: "Unspecified" };
const GEO_META = {
  us: "United States", uk: "United Kingdom", eu: "Europe (EU/EEA)", in: "India",
  ca: "Canada", worldwide_remote: "Worldwide · Remote",
  other_remote: "Other · Remote", other: "Other / Unspecified",
};
const GEO_ORDER = ["us", "uk", "eu", "in", "ca", "worldwide_remote", "other_remote", "other"];
const LEVEL_META = { entry: "Entry level", mid: "Mid level", senior: "Senior", leadership: "Leadership" };
const POSTED_OPTS = [["", "Any time"], ["24h", "Past 24 hours"], ["3d", "Past 3 days"], ["7d", "Past week"], ["14d", "Past 2 weeks"], ["30d", "Past month"]];
const SALARY_OPTS = [[0, "Any pay"], [40000, "$40k+"], [60000, "$60k+"], [80000, "$80k+"], [100000, "$100k+"], [150000, "$150k+"], [200000, "$200k+"]];
const STATUSES = [["saved", "Saved"], ["applied", "Applied"], ["interviewing", "Interviewing"], ["offer", "Offer"], ["rejected", "Rejected"]];

/* ---------------- utils ---------------- */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

function relTime(iso) {
  if (!iso) return "date unknown";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (isNaN(s)) return "date unknown";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)}d ago`;
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
}
function fmtDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return isNaN(d) ? null : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
function deadlineInfo(iso) {
  if (!iso) return null;
  const days = Math.ceil((new Date(iso).getTime() - Date.now()) / 86400000);
  if (days < 0) return { label: "Closed " + fmtDate(iso), cls: "b-expired" };
  if (days === 0) return { label: "Closes today", cls: "b-deadline-hot" };
  if (days <= 2) return { label: `${days}d left to apply`, cls: "b-deadline-hot" };
  if (days <= 7) return { label: `${days}d left to apply`, cls: "b-deadline-warn" };
  return { label: "Apply by " + fmtDate(iso), cls: "b-deadline-ok" };
}
function monogram(company) {
  const words = (company || "?").replace(/[^a-zA-Z0-9 &]/g, "").trim().split(/\s+/);
  const initials = (words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2)).toUpperCase();
  let h = 0; for (const c of company || "?") h = (h * 31 + c.charCodeAt(0)) % 997;
  const hue = h % 360;
  return { initials, bg: `linear-gradient(135deg, hsl(${hue} 48% 38%), hsl(${(hue + 40) % 360} 52% 26%))` };
}
function prettySource(name) {
  if (sourceLabels[name]) return sourceLabels[name];
  if (name.startsWith("gh:")) return name.slice(3) + " (Greenhouse)";
  if (name.startsWith("ashby:")) return name.slice(6) + " (Ashby)";
  return { remoteok: "RemoteOK", remotive: "Remotive", arbeitnow: "Arbeitnow", jobicy: "Jobicy", weworkremotely: "We Work Remotely", hn_hiring: "HN Who's Hiring", github_internships: "GitHub Lists" }[name] || name;
}
function toast(msg) {
  els.toast.textContent = msg;
  els.toast.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => els.toast.classList.remove("show"), 2400);
}
const ICON = {
  pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 12-9 12s-9-5-9-12a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>',
  ext: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  bm: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linejoin="round"><path d="M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
  bmf: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  zap: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
  layers: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
};

/* ---------------- application tracker ---------------- */
const TRACKER_KEY = "rr_tracker";
const tracker = {
  _read() { try { return JSON.parse(localStorage.getItem(TRACKER_KEY) || "{}"); } catch { return {}; } },
  _write(m) { localStorage.setItem(TRACKER_KEY, JSON.stringify(m)); renderTrackerCount(); },
  all() { return this._read(); },
  has(fp) { return !!this._read()[fp]; },
  get(fp) { return this._read()[fp]; },
  add(job, status = "saved") {
    const m = this._read();
    const prev = m[job.fingerprint] || {};
    m[job.fingerprint] = {
      job: { fingerprint: job.fingerprint, title: job.title, company: job.company,
             location: job.location || "", job_type: job.job_type || "",
             remote_mode: job.remote_mode || "", salary_label: job.salary_label || "",
             posted_at: job.posted_at || null, apply_url: job.apply_url || job.url || "",
             tags: job.tags || [] },
      status: prev.status || status,
      notes: prev.notes || "",
      saved_at: prev.saved_at || new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    this._write(m);
  },
  setStatus(fp, status) {
    const m = this._read();
    if (!m[fp]) return;
    m[fp].status = status; m[fp].updated_at = new Date().toISOString();
    this._write(m);
  },
  setNotes(fp, notes) {
    const m = this._read();
    if (!m[fp]) return;
    m[fp].notes = notes; m[fp].updated_at = new Date().toISOString();
    this._write(m);
  },
  remove(fp) {
    const m = this._read();
    if (m[fp]) { delete m[fp]; this._write(m); }
  },
  toggleSaved(job) {
    if (this.has(job.fingerprint)) { this.remove(job.fingerprint); toast("Removed from tracker"); }
    else { this.add(job, "saved"); toast("Added to tracker — set its status anytime"); }
    syncBookmarkUI(job.fingerprint);
  },
  count() { return Object.keys(this._read()).length; },
};
function syncBookmarkUI(fp) {
  const on = tracker.has(fp);
  document.querySelectorAll(`.bookmark[data-fp="${fp}"]`).forEach(b => {
    b.classList.toggle("saved", on);
    b.innerHTML = on ? ICON.bmf : ICON.bm;
  });
  const dBtn = $("drawerBookmark");
  if (dBtn && dBtn.dataset.fp === fp) dBtn.textContent = on ? "Saved ✓" : "Save";
  if (els.tracker.classList.contains("open")) renderTracker();
}
function migrateLegacySaved() {
  if (localStorage.getItem(TRACKER_KEY) || !localStorage.getItem("rr_saved")) return;
  try {
    const old = JSON.parse(localStorage.getItem("rr_saved") || "{}");
    Object.values(old).forEach(j => { if (j && j.fingerprint) tracker.add(j, "saved"); });
    if (Object.keys(old).length) toast(`Imported ${Object.keys(old).length} saved jobs into the tracker`);
  } catch { /* ignore */ }
  localStorage.removeItem("rr_saved");
}
function renderTrackerCount() {
  els.savedCount.textContent = tracker.count() ? `(${tracker.count()})` : "";
  els.trackerBtn.classList.toggle("active", tracker.count() > 0);
}

const CSV_HEADERS = ["Title", "Company", "Location", "Type", "Salary", "Status",
                     "Notes", "Saved", "Last Update", "Apply URL"];
function csvCell(v) {
  v = String(v ?? "");
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}
function exportCSV() {
  const items = Object.values(tracker.all());
  if (!items.length) return toast("Nothing to export yet");
  const statusName = Object.fromEntries(STATUSES);
  const rows = [CSV_HEADERS.join(",")];
  items.forEach(it => {
    const j = it.job;
    rows.push([j.title, j.company, j.location, TYPE_META[j.job_type]?.[0] || j.job_type,
               j.salary_label, statusName[it.status] || it.status, it.notes || "",
               fmtDate(it.saved_at) || "", fmtDate(it.updated_at) || "", j.apply_url]
              .map(csvCell).join(","));
  });
  const blob = new Blob(["﻿" + rows.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `roleradar-tracker-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  toast(`Exported ${items.length} tracked opportunities`);
}

function renderTracker() {
  const all = tracker.all();
  const byStatus = Object.fromEntries(STATUSES.map(([s]) => [s, []]));
  Object.entries(all).forEach(([fp, it]) => (byStatus[it.status] ||= []).push([fp, it]));
  for (const k of Object.keys(byStatus)) byStatus[k].sort((a, b) => (b[1].updated_at || "").localeCompare(a[1].updated_at || ""));

  els.trackerSub.textContent = tracker.count()
    ? `${tracker.count()} opportunities across ${STATUSES.filter(([s]) => byStatus[s].length).length} stages · stored locally in this browser`
    : "your pipeline, stored locally in this browser";

  els.trackerBoard.innerHTML = STATUSES.map(([status, label]) => {
    const items = byStatus[status];
    const body = items.length ? items.map(([fp, it]) => {
      const j = it.job, mg = monogram(j.company);
      return `<div class="t-card" data-fp="${esc(fp)}">
        <div class="t-card-title" data-open="${esc(fp)}" title="Open posting">${esc(j.title)}</div>
        <div class="t-card-co">
          <span class="monogram" style="background:${mg.bg};width:17px;height:17px;font-size:8.5px;border-radius:5px;display:inline-flex;vertical-align:-4px;margin-right:5px">${esc(mg.initials)}</span>${esc(j.company)}
          <span class="st-pill st-${status}" style="margin-left:6px">${label}</span>
        </div>
        <div class="t-card-meta">
          ${j.location ? `<span>${esc(j.location)}</span>` : ""}
          ${j.salary_label ? `<span>${esc(j.salary_label)}</span>` : ""}
          <span>saved ${relTime(it.saved_at)}</span>
        </div>
        <select data-status-for="${esc(fp)}">
          ${STATUSES.map(([v, l]) => `<option value="${v}" ${v === status ? "selected" : ""}>${l}</option>`).join("")}
        </select>
        <textarea data-notes-for="${esc(fp)}" placeholder="Notes — contacts, follow-ups, interview prep…" rows="2">${esc(it.notes || "")}</textarea>
        <div class="t-card-foot">
          ${j.apply_url ? `<a class="t-mini-btn" href="${esc(j.apply_url)}" target="_blank" rel="noopener noreferrer">${ICON.ext} Apply</a>` : ""}
          <button class="t-mini-btn danger" data-remove="${esc(fp)}" style="margin-left:auto">${ICON.trash} Remove</button>
        </div>
      </div>`;
    }).join("") : `<div class="t-empty">${status === "saved" ? "Bookmark opportunities and they'll land here." : "Nothing here yet."}</div>`;
    return `<div class="t-col t-${status}">
      <div class="t-col-head"><span class="t-dot"></span>${label}<span class="t-n">${items.length}</span></div>
      <div class="t-col-body">${body}</div>
    </div>`;
  }).join("");

  els.trackerBoard.querySelectorAll("select[data-status-for]").forEach(sel => {
    sel.onchange = () => { tracker.setStatus(sel.dataset.statusFor, sel.value); renderTracker(); toast(`Moved to ${sel.options[sel.selectedIndex].text}`); };
  });
  els.trackerBoard.querySelectorAll("textarea[data-notes-for]").forEach(ta => {
    ta.onchange = () => { tracker.setNotes(ta.dataset.notesFor, ta.value); };
  });
  els.trackerBoard.querySelectorAll("[data-remove]").forEach(btn => {
    btn.onclick = () => { const fp = btn.dataset.remove; tracker.remove(fp); syncBookmarkUI(fp); renderTracker(); };
  });
  els.trackerBoard.querySelectorAll("[data-open]").forEach(el => {
    el.onclick = () => { closeTracker(); openJob(el.dataset.open); };
  });
}
function openTracker() { renderTracker(); els.tracker.classList.add("open"); els.trackerOverlay.classList.add("open"); document.body.style.overflow = "hidden"; }
function closeTracker() { els.tracker.classList.remove("open"); els.trackerOverlay.classList.remove("open"); document.body.style.overflow = ""; }

/* ---------------- URL sync ---------------- */
function toURL() {
  const p = new URLSearchParams();
  if (state.q) p.set("q", state.q);
  for (const [k, arr] of [["type", state.type], ["remote", state.remote], ["geo", state.geo],
                          ["level", state.level], ["source", state.sources]])
    if (arr.length) p.set(k, arr.join(","));
  if (state.posted) p.set("posted", state.posted);
  if (state.min_salary) p.set("min_salary", state.min_salary);
  if (state.has_salary) p.set("has_salary", "1");
  if (state.include_expired) p.set("include_expired", "1");
  if (state.sort !== "newest") p.set("sort", state.sort);
  if (state.tag) p.set("tag", state.tag);
  const s = p.toString();
  history.replaceState(null, "", s ? "?" + s : location.pathname);
}
function fromURL() {
  const p = new URLSearchParams(location.search);
  state.q = p.get("q") || "";
  state.type = (p.get("type") || "").split(",").filter(Boolean);
  state.remote = (p.get("remote") || "").split(",").filter(Boolean);
  state.geo = (p.get("geo") || "").split(",").filter(Boolean);
  state.level = (p.get("level") || "").split(",").filter(Boolean);
  state.sources = (p.get("source") || "").split(",").filter(Boolean);
  state.posted = p.get("posted") || "";
  state.min_salary = parseInt(p.get("min_salary") || "0", 10) || 0;
  state.has_salary = p.get("has_salary") === "1";
  state.include_expired = p.get("include_expired") === "1";
  state.sort = p.get("sort") || "newest";
  state.tag = (p.get("tag") || "").toLowerCase();
  els.q.value = state.q;
  els.sortSel.value = state.sort;
  return p.get("job");   // fingerprint or numeric id
}

function queryString(extra = {}) {
  const p = new URLSearchParams();
  const st = { ...state, ...extra };
  if (st.q) p.set("q", st.q);
  if (st.type.length) p.set("type", st.type.join(","));
  if (st.remote.length) p.set("remote", st.remote.join(","));
  if (st.geo.length) p.set("geo", st.geo.join(","));
  if (st.level.length) p.set("level", st.level.join(","));
  if (st.sources.length) p.set("source", st.sources.join(","));
  if (st.posted) p.set("posted", st.posted);
  if (st.min_salary) p.set("min_salary", st.min_salary);
  if (st.has_salary) p.set("has_salary", "1");
  if (st.include_expired) p.set("include_expired", "1");
  if (st.sort) p.set("sort", st.sort);
  if (st.tag) p.set("tag", st.tag);
  p.set("page", st.page); p.set("per_page", 20);
  return p.toString();
}

/* ---------------- fetching ---------------- */
async function fetchJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function loadResults(reset = true) {
  if (loading) return;
  loading = true;
  if (reset) { state.page = 1; reachedEnd = false; els.cards.innerHTML = skeletons(5); }
  els.loadMore.style.display = "none";
  try {
    const data = await fetchJSON(`/api/jobs?${queryString()}`);
    lastFacets = data.facets || {}; lastTotal = data.total;
    if (reset) els.cards.innerHTML = "";
    renderCards(data.results, !reset);
    renderMeta(data);
    renderAllFilters();
    renderActiveChips();
    reachedEnd = data.page * data.per_page >= data.total;
    els.loadMore.style.display = reachedEnd || !data.total ? "none" : "";
  } catch (e) {
    if (reset) els.cards.innerHTML = emptyState("Couldn't reach the search API", String(e));
    renderMeta(null);
  }
  loading = false;
}

function skeletons(n) {
  return Array.from({ length: n }, () => `
    <div class="sk-card">
      <div style="display:flex;gap:14px">
        <div class="sk" style="width:44px;height:44px;border-radius:12px"></div>
        <div style="flex:1">
          <div class="sk" style="height:16px;width:55%;margin-bottom:9px"></div>
          <div class="sk" style="height:12px;width:35%"></div>
          <div style="display:flex;gap:6px;margin-top:14px">
            <div class="sk" style="height:20px;width:74px;border-radius:999px"></div>
            <div class="sk" style="height:20px;width:60px;border-radius:999px"></div>
            <div class="sk" style="height:20px;width:86px;border-radius:999px"></div>
          </div>
        </div>
      </div>
    </div>`).join("");
}

function emptyState(title, sub) {
  return `<div class="empty">
    <svg viewBox="0 0 32 32" fill="none"><circle cx="16" cy="16" r="14" stroke="#6366f1" stroke-width="1.6" opacity=".7"/><circle cx="16" cy="16" r="8.5" stroke="#22d3ee" stroke-width="1.2" opacity=".5"/><line x1="16" y1="16" x2="25.5" y2="8.5" stroke="#6366f1" stroke-width="1.8" stroke-linecap="round"/><circle cx="16" cy="16" r="2" fill="#22d3ee"/></svg>
    <h3>${esc(title)}</h3><p>${sub || "Try broadening your search, removing a few filters, or hitting Sync to re-crawl every source."}</p>
  </div>`;
}

/* ---------------- card rendering ---------------- */
function typeBadge(t) {
  const m = TYPE_META[t] || TYPE_META.other;
  return `<span class="badge ${m[1]}">${m[0]}</span>`;
}
function remoteBadge(r) {
  if (r === "remote") return `<span class="badge b-remote">Remote</span>`;
  if (r === "hybrid") return `<span class="badge b-hybrid">Hybrid</span>`;
  if (r === "onsite") return `<span class="badge b-onsite">On-site</span>`;
  return "";
}
function cardHTML(job) {
  const mg = monogram(job.company);
  const dl = deadlineInfo(job.deadline);
  const isNew = job.first_seen && (Date.now() - new Date(job.first_seen)) < 86400000;
  const tags = (job.tags || []).filter(t => t && t.length < 30);
  const seenPills = [...new Set((job.sources || []).map(s => s.source))]
    .slice(0, 3).map(s => `<span class="seen-pill">${esc(shortSource(s))}</span>`).join("");
  const tracked = tracker.has(job.fingerprint);
  return `<article class="card ${job.status === "expired" ? "expired" : ""}" data-fp="${esc(job.fingerprint)}">
    <button class="bookmark ${tracked ? "saved" : ""}" data-fp="${esc(job.fingerprint)}" title="Add to tracker">
      ${tracked ? ICON.bmf : ICON.bm}
    </button>
    <div class="card-top">
      <div class="monogram" style="background:${mg.bg}">${esc(mg.initials)}</div>
      <div class="card-main">
        <h3 class="card-title">${esc(job.title)}</h3>
        <div class="card-sub">
          <span class="company">${esc(job.company)}</span>
          ${job.location ? `<span class="loc">${ICON.pin} ${esc(job.location)}</span>` : ""}
        </div>
      </div>
    </div>
    <div class="badges">
      ${typeBadge(job.job_type)}${remoteBadge(job.remote_mode)}
      ${job.level && job.level !== "mid" ? `<span class="badge b-level">${LEVEL_META[job.level] || job.level}</span>` : ""}
      ${job.salary_label ? `<span class="badge b-salary">${esc(job.salary_label)}</span>` : ""}
      ${job.extra && job.extra.visa_sponsorship ? `<span class="badge b-visa">Visa sponsorship</span>` : ""}
      ${isNew ? `<span class="badge b-new">${ICON.zap} New</span>` : ""}
      ${dl ? `<span class="badge ${dl.cls}">${esc(dl.label)}</span>` : ""}
      ${job.status === "expired" ? `<span class="badge b-expired">${esc(job.expired_reason || "Likely expired")}</span>` : ""}
    </div>
    ${job.excerpt ? `<p class="card-excerpt">${esc(job.excerpt)}</p>` : ""}
    ${tags.length ? `<div class="card-tags">${tags.slice(0, 6).map(t => `<span class="ctag">${esc(t)}</span>`).join("")}${tags.length > 6 ? `<span class="ctag more">+${tags.length - 6} more</span>` : ""}</div>` : ""}
    <div class="card-foot">
      <span class="time">${ICON.clock} ${relTime(job.posted_at)}</span>
      ${job.num_sources > 1 ? `<span class="time">${ICON.layers} seen on ${job.num_sources} sources</span>` : ""}
      <span class="seen-on">${seenPills}</span>
    </div>
  </article>`;
}
function shortSource(name) {
  if (name.startsWith("gh:")) return name.slice(3);
  if (name.startsWith("ashby:")) return name.slice(6);
  return { weworkremotely: "WWR", hn_hiring: "HN", github_internships: "GitHub" }[name] || name;
}

function renderCards(results, append) {
  if (!results.length && !append) {
    els.cards.innerHTML = emptyState("No opportunities match", null);
    return;
  }
  results.forEach(j => jobCache.set(j.fingerprint, j));
  const html = results.map(cardHTML).join("");
  if (append) els.cards.insertAdjacentHTML("beforeend", html);
  else els.cards.innerHTML = html;
}

function renderMeta(data) {
  if (!data) { els.resultMeta.innerHTML = ""; return; }
  const bits = [`<b>${data.total.toLocaleString()}</b> opportunities`, `${data.took_ms} ms`];
  if (lastFacets.new_24h) bits.push(`<b>${lastFacets.new_24h}</b> new in 24h`);
  if (lastFacets.with_salary) bits.push(`<b>${lastFacets.with_salary}</b> with pay info`);
  els.resultMeta.innerHTML = bits.join('<span class="sep">·</span>') +
    (hasFilters() ? ` <span class="sep">·</span> <button class="clear-link" id="clearAll">clear all filters</button>` : "");
  const c = $("clearAll");
  if (c) c.onclick = () => { clearFilters(); loadResults(); };
}
function hasFilters() {
  return !!(state.q || state.type.length || state.remote.length || state.geo.length ||
    state.level.length || state.sources.length || state.posted || state.min_salary ||
    state.has_salary || state.include_expired || state.tag);
}
function clearFilters() {
  Object.assign(state, { q: "", type: [], remote: [], geo: [], level: [], sources: [], posted: "", min_salary: 0, has_salary: false, include_expired: false, tag: "" });
  els.q.value = ""; toURL();
}

/* ---------------- filters ---------------- */
function checkboxRow(kind, value, label, count, swatch) {
  const on = state[kind].includes(value);
  return `<label class="f-opt"><input type="checkbox" data-kind="${kind}" value="${esc(value)}" ${on ? "checked" : ""}>
    ${swatch ? `<span class="swatch" style="background:${swatch}"></span>` : ""}${esc(label)}
    ${count != null ? `<span class="n">${count.toLocaleString()}</span>` : ""}</label>`;
}

function renderAllFilters() {
  const F = lastFacets;
  const typeSw = { fulltime: "#818cf8", parttime: "#38bdf8", contract: "#fbbf24", internship: "#34d399", freelance: "#a78bfa", apprenticeship: "#fb923c", research: "#f472b6", gig: "#94a3b8", other: "#94a3b8" };
  const typeOrder = Object.keys(TYPE_META);
  $("fType").innerHTML = `<div class="f-title">Opportunity type</div>` +
    typeOrder.filter(t => (F.type || {})[t]).map(t =>
      checkboxRow("type", t, TYPE_META[t][0], F.type[t], typeSw[t])).join("");

  const remOrder = ["remote", "hybrid", "onsite"];
  $("fRemote").innerHTML = `<div class="f-title">Work mode</div>` +
    remOrder.filter(r => (F.remote || {})[r]).map(r =>
      checkboxRow("remote", r, REMOTE_META[r], F.remote[r])).join("");

  const geoSw = { us: "#60a5fa", uk: "#f472b6", eu: "#a78bfa", in: "#fb923c", ca: "#f87171", worldwide_remote: "#2dd4bf", other_remote: "#94a3b8", other: "#64748b" };
  $("fGeo").innerHTML = `<div class="f-title">Region</div>` +
    GEO_ORDER.filter(g => (F.geo || {})[g]).map(g =>
      checkboxRow("geo", g, GEO_META[g], F.geo[g], geoSw[g])).join("");

  const lvlOrder = ["entry", "mid", "senior", "leadership"];
  $("fLevel").innerHTML = `<div class="f-title">Experience</div>` +
    lvlOrder.filter(l => (F.level || {})[l]).map(l =>
      checkboxRow("level", l, LEVEL_META[l], F.level[l])).join("");

  $("fWhen").innerHTML = `<div class="f-title">Freshness</div>
    <select class="select f-select" id="postedSel">
      ${POSTED_OPTS.map(([v, l]) => `<option value="${v}" ${state.posted === v ? "selected" : ""}>${l}</option>`).join("")}
    </select>
    <label class="f-opt"><input type="checkbox" id="expiredChk" ${state.include_expired ? "checked" : ""}> Include expired / outdated</label>`;

  $("fPay").innerHTML = `<div class="f-title">Compensation</div>
    <select class="select f-select" id="salSel">
      ${SALARY_OPTS.map(([v, l]) => `<option value="${v}" ${state.min_salary === v ? "selected" : ""}>${l}</option>`).join("")}
    </select>
    <label class="f-opt"><input type="checkbox" id="salOnlyChk" ${state.has_salary ? "checked" : ""}> Only show disclosed pay <span class="n">${(F.with_salary || 0).toLocaleString()}</span></label>`;

  const tags = F.tags || [];
  $("fTags").innerHTML = tags.length ? `<div class="f-title">Trending skills</div><div class="tag-cloud">` +
    tags.map(t => `<button class="tag-chip ${state.tag === t ? "active" : ""}" data-tag="${esc(t)}">${esc(t)}</button>`).join("") + `</div>` : "";

  const srcs = Object.entries(F.sources || {});
  $("fSources").innerHTML = `<div class="f-title">Sources <span class="f-count">${srcs.length}</span></div>
    <div class="f-sources">` +
    srcs.map(([name, count]) => checkboxRow("sources", name, prettySource(name), count)).join("") + `</div>`;

  $("postedSel").onchange = (e) => { state.posted = e.target.value; toURL(); loadResults(); };
  $("expiredChk").onchange = (e) => { state.include_expired = e.target.checked; toURL(); loadResults(); };
  $("salSel").onchange = (e) => { state.min_salary = +e.target.value; toURL(); loadResults(); };
  $("salOnlyChk").onchange = (e) => { state.has_salary = e.target.checked; toURL(); loadResults(); };

  document.querySelectorAll('#filters input[type="checkbox"][data-kind]').forEach(cb => {
    cb.onchange = () => {
      const k = cb.dataset.kind, v = cb.value;
      state[k] = cb.checked ? [...new Set([...state[k], v])] : state[k].filter(x => x !== v);
      toURL(); loadResults();
    };
  });
  document.querySelectorAll("#fTags .tag-chip").forEach(ch => {
    ch.onclick = () => {
      state.tag = state.tag === ch.dataset.tag ? "" : ch.dataset.tag;
      toURL(); loadResults();
    };
  });
}

function renderActiveChips() {
  const chips = [];
  const rm = (fn, label) => chips.push(`<span class="af-chip">${esc(label)}<button data-rm data-fn="${fn}" title="Remove">${ICON.x}</button></span>`);
  if (state.q) rm("q", `“${state.q}”`);
  if (state.tag) rm("tag", `#${state.tag}`);
  state.type.forEach(v => rm("type:" + v, TYPE_META[v]?.[0] || v));
  state.remote.forEach(v => rm("remote:" + v, REMOTE_META[v] || v));
  state.geo.forEach(v => rm("geo:" + v, GEO_META[v] || v));
  state.level.forEach(v => rm("level:" + v, LEVEL_META[v] || v));
  state.sources.forEach(v => rm("source:" + v, prettySource(v)));
  if (state.posted) rm("posted", POSTED_OPTS.find(o => o[0] === state.posted)?.[1] || state.posted);
  if (state.min_salary) rm("salary", `≥ $${(state.min_salary / 1000)}k est.`);
  if (state.has_salary) rm("has_salary", "Disclosed pay");
  if (state.include_expired) rm("expired", "Including expired");
  els.activeFilters.innerHTML = chips.join("");
  els.activeFilters.querySelectorAll("[data-rm]").forEach(btn => {
    btn.onclick = () => {
      const [kind, val] = btn.dataset.fn.split(":");
      if (kind === "q") { state.q = ""; els.q.value = ""; }
      else if (kind === "tag") state.tag = "";
      else if (kind === "posted") state.posted = "";
      else if (kind === "salary") state.min_salary = 0;
      else if (kind === "has_salary") state.has_salary = false;
      else if (kind === "expired") state.include_expired = false;
      else if (["type", "remote", "geo", "level", "source"].includes(kind)) {
        const k = kind + (kind === "source" ? "s" : "");
        state[k] = state[k].filter(x => x !== val);
      }
      toURL(); loadResults();
    };
  });
}

/* ---------------- drawer ---------------- */
const ALLOWED_TAGS = new Set(["P", "BR", "UL", "OL", "LI", "STRONG", "B", "EM", "I", "U", "H1", "H2", "H3", "H4", "H5", "BLOCKQUOTE", "A", "CODE", "PRE", "TABLE", "THEAD", "TBODY", "TR", "TD", "TH", "DIV", "SPAN", "HR", "SUP", "SUB"]);
function sanitizeHTML(html) {
  const doc = new DOMParser().parseFromString(`<div>${html || ""}</div>`, "text/html");
  const root = doc.body.firstChild;
  const walk = (node) => {
    [...node.children].forEach((el) => {
      if (!ALLOWED_TAGS.has(el.tagName)) {
        if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "FORM", "INPUT", "BUTTON", "SELECT", "TEXTAREA", "IMG", "VIDEO", "AUDIO", "SVG"].includes(el.tagName)) el.remove();
        else { el.replaceWith(...el.childNodes); walk(node); }
        return;
      }
      [...el.attributes].forEach(a => {
        const keep = el.tagName === "A" && a.name === "href" && /^(https?:|mailto:)/i.test(a.value);
        if (!keep) el.removeAttribute(a.name);
      });
      if (el.tagName === "A") { el.setAttribute("target", "_blank"); el.setAttribute("rel", "noopener noreferrer"); }
      walk(el);
    });
  };
  walk(root);
  return root.innerHTML;
}

async function openJob(fpOrId, push = true) {
  els.drawerHead.innerHTML = `<div class="sk" style="height:22px;width:60%;margin-bottom:12px"></div><div class="sk" style="height:14px;width:35%"></div>`;
  els.drawerBody.innerHTML = `<div class="sk" style="height:110px;border-radius:12px;margin-bottom:16px"></div><div class="sk" style="height:14px;width:90%;margin-bottom:10px"></div><div class="sk" style="height:14px;width:80%;margin-bottom:10px"></div><div class="sk" style="height:14px;width:85%"></div>`;
  showDrawer();
  let job;
  try { job = await fetchJSON(`/api/jobs/${encodeURIComponent(fpOrId)}`); }
  catch {
    els.drawerHead.innerHTML = `<div class="d-title">Couldn't load this opportunity</div>`;
    els.drawerBody.innerHTML = `<p class="d-loc">It may have rotated out — try refreshing or hit Sync to re-crawl every source.</p>`;
    return;
  }
  const mg = monogram(job.company);
  const dl = deadlineInfo(job.deadline);
  const periodMap = { year: "per year", month: "per month", hour: "per hour", week: "per week", day: "per day" };
  const facts = [
    ["Opportunity type", (TYPE_META[job.job_type] || [job.job_type])[0]],
    ["Experience", LEVEL_META[job.level] || "Not specified"],
    ["Location", job.location || "Not specified"],
    ["Region", GEO_META[job.geo] || job.geo || "—"],
    ["Work mode", REMOTE_META[job.remote_mode] || job.remote_mode],
    ["Compensation", job.salary_label ? `${job.salary_label} ${job.salary_period ? "· " + periodMap[job.salary_period] : ""} ${job.salary_currency ? "· " + job.salary_currency : ""}` : "Not disclosed", !!job.salary_label],
    ["Posted", job.posted_at ? `${relTime(job.posted_at)} (${fmtDate(job.posted_at)})` : "Unknown"],
    ["Deadline", dl ? `${dl.label}${job.deadline ? " · " + fmtDate(job.deadline) : ""}` : "Not specified"],
  ];
  const trackedNow = tracker.has(job.fingerprint);
  els.drawerHead.innerHTML = `
    <button class="drawer-close" id="drawerClose" title="Close (Esc)">${ICON.x}</button>
    <h2 class="d-title">${esc(job.title)}</h2>
    <div class="d-company-row">
      <div class="monogram" style="background:${mg.bg};width:38px;height:38px;font-size:13px;border-radius:10px">${esc(mg.initials)}</div>
      <div><div class="d-company">${esc(job.company)}</div>
      <div class="d-loc">${esc(job.location || (job.remote_mode === "remote" ? "Remote" : "Location unspecified"))}</div></div>
    </div>
    <div class="badges" style="margin-top:14px">
      ${typeBadge(job.job_type)}${remoteBadge(job.remote_mode)}
      ${job.salary_label ? `<span class="badge b-salary">${esc(job.salary_label)}</span>` : ""}
      ${job.status === "expired" ? `<span class="badge b-expired">${esc(job.expired_reason || "Likely expired")}</span>` : ""}
    </div>
    <div class="d-actions">
      ${job.status !== "expired" ? `<a class="btn-primary" href="${esc(job.apply_url || job.url)}" target="_blank" rel="noopener noreferrer">Apply now ${ICON.ext}</a>` : ""}
      ${job.url ? `<a class="btn-ghost" href="${esc(job.url)}" target="_blank" rel="noopener noreferrer">Original posting ${ICON.ext}</a>` : ""}
      <button class="btn-ghost" id="copyJobLink">${ICON.link} Copy link</button>
      <button class="btn-ghost" id="drawerBookmark" data-fp="${esc(job.fingerprint)}">${trackedNow ? "Saved ✓" : "Save"}</button>
      <select class="btn-ghost" id="drawerStatus" style="appearance:auto;cursor:pointer" title="Tracker status">
        ${STATUSES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}
      </select>
    </div>`;
  els.drawerBody.innerHTML = `
    <div class="facts">${facts.filter(f => f[1]).map(([k, v, money]) =>
      `<div class="fact"><div class="k">${esc(k)}</div><div class="v ${money ? "money" : ""}">${esc(v)}</div></div>`).join("")}</div>
    ${(job.tags || []).length ? `<div class="d-section-title">Skills & tags</div>
      <div class="card-tags" style="margin:0">${job.tags.map(t => `<span class="ctag">${esc(t)}</span>`).join("")}</div>` : ""}
    <div class="d-section-title">Full description</div>
    <div class="d-desc" id="dDesc"></div>
    <div class="d-section-title">Where we found it</div>
    <div class="source-list">
      ${(job.sources || []).map(s => `<div class="source-row">
        <span class="platform">${esc(prettySource(s.source))}</span>
        <span class="meta">${s.posted_at ? "posted " + relTime(s.posted_at) : ""}</span>
        <a href="${esc(s.url || s.apply_url)}" target="_blank" rel="noopener noreferrer">view ${ICON.ext.replace('width="2.2"', 'width="2.2" style="width:11px;height:11px;vertical-align:-1px"')}</a>
      </div>`).join("")}
    </div>`;
  $("dDesc").innerHTML = job.description_html
    ? sanitizeHTML(job.description_html)
    : `<p>${esc(job.description_text || "No description provided by the source.").replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>")}</p>`;
  $("drawerClose").onclick = hideDrawer;
  $("copyJobLink").onclick = () => {
    const u = new URL(location.href); u.searchParams.set("job", job.fingerprint);
    navigator.clipboard?.writeText(u.toString()).then(() => toast("Permanent link copied"));
  };
  const bookmarkBtn = $("drawerBookmark");
  bookmarkBtn.onclick = () => {
    tracker.toggleSaved({ ...job, excerpt: (job.description_text || "").slice(0, 240) });
    bookmarkBtn.textContent = tracker.has(job.fingerprint) ? "Saved ✓" : "Save";
  };
  const statusSel = $("drawerStatus");
  const cur = tracker.get(job.fingerprint);
  if (cur) statusSel.value = cur.status;
  statusSel.onchange = () => {
    const snap = jobCache.get(job.fingerprint) || { ...job, excerpt: (job.description_text || "").slice(0, 240) };
    tracker.add(snap, statusSel.value);
    tracker.setStatus(job.fingerprint, statusSel.value);
    syncBookmarkUI(job.fingerprint);
    bookmarkBtn.textContent = "Saved ✓";
    bookmarkBtn.classList.add("saved");
    toast(`Tracker: moved to ${statusSel.options[statusSel.selectedIndex].text}`);
  };
  if (push) {
    const u = new URL(location.href); u.searchParams.set("job", job.fingerprint);
    history.replaceState(null, "", u);
  }
}
function showDrawer() {
  els.drawer.classList.add("open"); els.drawerOverlay.classList.add("open");
  els.drawer.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
}
function hideDrawer() {
  els.drawer.classList.remove("open"); els.drawerOverlay.classList.remove("open");
  els.drawer.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
  const u = new URL(location.href); u.searchParams.delete("job");
  history.replaceState(null, "", u);
}

/* ---------------- stats & sync ---------------- */
async function loadStats() {
  try {
    const s = await fetchJSON("/api/stats");
    const syncBusy = s.sync && s.sync.sync_running;
    els.syncDot.className = "dot" + (syncBusy ? " busy" : "");
    const ago = s.last_sync ? relTime(s.last_sync) : "never";
    $("syncLabel").innerHTML = syncBusy ? "syncing sources…" : `synced ${ago}`;
    const chips = [
      `<b>${(s.total_active || 0).toLocaleString()}</b> live`,
      `<b>${(s.new_24h || 0).toLocaleString()}</b> new 24h`,
      `<b>${(s.companies || 0).toLocaleString()}</b> companies`,
      `<b>${(s.internships || 0).toLocaleString()}</b> internships`,
    ];
    const existing = $("syncLabel").parentElement;
    els.statChips.innerHTML = "";
    els.statChips.appendChild(existing);
    chips.forEach(h => { const el = document.createElement("span"); el.className = "stat-chip"; el.innerHTML = h; els.statChips.appendChild(el); });

    const runs = s.source_runs || {};
    const names = Object.keys(s.by_platform || {});
    els.sourcesStrip.innerHTML =
      `<span class="s-pill">${ICON.layers.replace("<svg", '<svg style="width:11px;height:11px"')} ${names.length} live sources</span>` +
      names.slice(0, 40).map(n => {
        const r = runs[n] || {};
        return `<span class="s-pill ${r.status === "error" ? "err" : ""}" title="${esc(prettySource(n))}: ${r.fetched ?? "?"} listings fetched ${r.finished_at ? relTime(r.finished_at) : ""}"><span class="dot"></span>${esc(shortSource(n))}</span>`;
      }).join("");
    return syncBusy;
  } catch { return false; }
}

async function pollSync() {
  const busy = await loadStats();
  setTimeout(pollSync, busy ? 4000 : 45000);
}

async function manualRefresh() {
  const btn = $("refreshBtn");
  if (btn.classList.contains("spinning")) return;
  btn.classList.add("spinning");
  try {
    await fetchJSON("/api/refresh", { method: "POST" });
    toast("Re-crawling every source — this takes ~1 min with 60+ boards");
    for (let i = 0; i < 40; i++) {
      await new Promise(r => setTimeout(r, 3000));
      const st = await fetchJSON("/api/sync");
      await loadStats();
      if (!st.sync_running) break;
    }
    toast("Sync complete");
    loadResults();
  } catch (e) { toast("Sync failed: " + e.message); }
  btn.classList.remove("spinning");
}

/* ---------------- sources metadata ---------------- */
async function loadSourceLabels() {
  try {
    const d = await fetchJSON("/api/sources");
    (d.enabled || []).forEach(s => sourceLabels[s.name] = s.label);
  } catch { /* labels fall back to prettified names */ }
}

/* ---------------- events ---------------- */
function bindEvents() {
  els.q.addEventListener("input", debounce(() => {
    state.q = els.q.value.trim(); toURL(); loadResults();
  }, 280));

  els.sortSel.onchange = () => { state.sort = els.sortSel.value; toURL(); loadResults(); };

  els.loadMore.onclick = () => { state.page += 1; loadResults(false); };
  const io = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && !loading && !reachedEnd && lastTotal) {
      state.page += 1; loadResults(false);
    }
  }, { rootMargin: "600px" });
  io.observe($("sentinel"));

  els.cards.addEventListener("click", (e) => {
    const bm = e.target.closest(".bookmark");
    if (bm) {
      e.stopPropagation();
      const fp = bm.dataset.fp;
      const card = bm.closest(".card");
      const jobPreview = jobCache.get(fp) || {
        fingerprint: fp,
        title: card.querySelector(".card-title")?.textContent || "",
        company: card.querySelector(".company")?.textContent || "",
        tags: [], job_type: "", remote_mode: "",
      };
      tracker.toggleSaved(jobPreview);
      return;
    }
    const card = e.target.closest(".card");
    if (card && card.dataset.fp) openJob(card.dataset.fp);
  });
  els.drawerOverlay.onclick = hideDrawer;
  els.trackerOverlay.onclick = closeTracker;

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { hideDrawer(); closeTracker(); els.filters.classList.remove("open"); }
    if (e.key === "/" && document.activeElement !== els.q && !els.tracker.classList.contains("open")) { e.preventDefault(); els.q.focus(); }
  });

  $("refreshBtn").onclick = manualRefresh;
  $("shareBtn").onclick = () => navigator.clipboard?.writeText(location.href).then(() => toast("Search link copied"));
  els.trackerBtn.onclick = openTracker;
  $("trackerClose").onclick = closeTracker;
  $("exportCsv").onclick = exportCSV;
  els.fab.onclick = () => els.filters.classList.toggle("open");
}

/* ---------------- boot ---------------- */
(async function boot() {
  const jobParam = fromURL();
  migrateLegacySaved();
  bindEvents();
  renderTrackerCount();
  await loadSourceLabels();
  loadStats(); pollSync();
  await loadResults();
  if (jobParam) openJob(jobParam, false);
  // bookmark states (cards rendered before tracker migration finishes)
  document.querySelectorAll(".card .bookmark").forEach(b => syncBookmarkUI(b.dataset.fp));
})();
})();
