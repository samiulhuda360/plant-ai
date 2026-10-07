/* plant-ai dashboard: polls the historian API and draws the mimic, trends, alarms, incidents and assistant. */
"use strict";

const PRIO_COLOR = { Critical: "#b3261e", High: "#d4711c", Medium: "#c99a10", Low: "#4f7896" };
const PRIO_RANK = { Low: 0, Medium: 1, High: 2, Critical: 3 };
const SERIES = ["#2f6f68", "#4f7896", "#b5832a", "#a34a3c", "#6b7d3a", "#5f6d6a"];
const SVGNS = "http://www.w3.org/2000/svg";

let META = null;
let OVERVIEW = null;
let ALARMS = [];
let selectedAlarm = null;
let alarmFilter = "active";
let trendHours = 6;
let trendTags = ["AIT-301", "AIT-401", "AIT-201", "FIT-101"];
let chart = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const fmtTs = (ts) => (ts ? new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ") : "--");
const hhmm = (ts) => (ts ? new Date(ts * 1000).toISOString().slice(11, 16) : "--");
function fmtVal(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  const a = Math.abs(v);
  return a >= 1000 ? v.toFixed(0) : a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

/* ------------------------------------------------------------------ mimic */
const UNITS = [
  { id: "ds", x: 15, y: 18, w: 190, h: 132, label: "Dye-house dispensing", tags: ["DS-900", "FQ-901"], levels: ["LIT-901", "LIT-902", "LIT-903"] },
  { id: "inf", x: 15, y: 182, w: 160, h: 158, label: "Influent", tags: ["FIT-101", "AIT-101", "TT-101", "AIT-102", "AIT-103"] },
  { id: "eq", x: 205, y: 170, w: 160, h: 170, label: "Equalisation", tags: ["FV-101", "ZT-101", "FIT-102"], tank: "LIT-101", tankMax: 5 },
  { id: "acid", x: 395, y: 18, w: 140, h: 112, label: "Acid day tank", tags: ["P-201_SP"], tank: "LIT-201", tankMax: 100 },
  { id: "ph", x: 395, y: 200, w: 140, h: 140, label: "pH correction", tags: ["AIT-201", "AIT-201B"] },
  { id: "pac", x: 560, y: 18, w: 140, h: 112, label: "PAC day tank", tags: [], tank: "LIT-202", tankMax: 100 },
  { id: "coag", x: 560, y: 200, w: 140, h: 140, label: "Coagulation / floc", tags: ["P-202_SP", "FIT-202"] },
  { id: "blower", x: 745, y: 18, w: 150, h: 112, label: "Blower B-301", tags: ["SC-301", "JT-301"], motor: "B-301_RUN" },
  { id: "aer", x: 725, y: 200, w: 190, h: 140, label: "Aeration basin", tags: ["AIT-301"] },
  { id: "clar", x: 945, y: 200, w: 125, h: 140, label: "Clarifier", tags: ["AIT-401"] },
  { id: "dis", x: 1100, y: 150, w: 128, h: 190, label: "Discharge", tags: ["AIT-501", "AIT-502", "TT-501", "FIT-501"] },
];
const SHORT = {
  "FIT-101": "Flow", "AIT-101": "pH", "TT-101": "Temp", "AIT-102": "Cond", "AIT-103": "Turb",
  "LIT-101": "Level", "FV-101": "Valve cmd", "ZT-101": "Valve pos", "FIT-102": "Fwd flow",
  "AIT-201": "pH A", "AIT-201B": "pH B", "P-201_SP": "P-201", "P-202_SP": "P-202 cmd", "FIT-202": "PAC flow",
  "SC-301": "Speed", "JT-301": "Power", "AIT-301": "DO", "AIT-401": "Turbidity",
  "AIT-501": "pH", "AIT-502": "COD", "TT-501": "Temp", "FIT-501": "Flow", "DS-900": "State", "FQ-901": "Batches",
  "LIT-901": "Soda", "LIT-902": "Brine", "LIT-903": "Acetic", "LIT-201": "Level", "LIT-202": "Level",
};
const UNIT_OF = {};
const valueNodes = {};

function el(name, attrs, parent) {
  const n = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}

function buildMimic() {
  const svg = $("mimic");
  svg.innerHTML = "";
  // process pipes (main flow path at y = 290)
  const pipeY = 292;
  const segs = [[175, 205], [365, 395], [530, 560], [695, 725], [915, 945], [1070, 1100]];
  segs.forEach(([a, b]) => {
    el("line", { x1: a, y1: pipeY, x2: b, y2: pipeY, class: "pipe" }, svg);
    el("path", { d: `M${b - 8},${pipeY - 5} L${b},${pipeY} L${b - 8},${pipeY + 5}`, fill: "#7d949a" }, svg);
  });
  el("line", { x1: 95, y1: 150, x2: 95, y2: 182, class: "pipe" }, svg); // dye-house drains to influent
  el("text", { x: 102, y: 172, class: "val-name" }, svg).textContent = "dye dumps";
  // chemical and air lines
  el("path", { d: "M462,130 L462,200", class: "chem-pipe" }, svg);
  el("path", { d: "M627,130 L627,200", class: "chem-pipe" }, svg);
  el("path", { d: "M820,130 L820,200", class: "chem-pipe" }, svg);
  el("text", { x: 468, y: 170, class: "val-name" }, svg).textContent = "H2SO4";
  el("text", { x: 633, y: 170, class: "val-name" }, svg).textContent = "PAC";
  el("text", { x: 826, y: 170, class: "val-name" }, svg).textContent = "air";

  for (const u of UNITS) {
    const g = el("g", { id: `u-${u.id}` }, svg);
    el("rect", { x: u.x, y: u.y, width: u.w, height: u.h, rx: 6, class: "unit" }, g);
    el("rect", { x: u.x - 2, y: u.y - 2, width: u.w + 4, height: u.h + 4, rx: 7, class: "alarm-box", id: `ab-${u.id}`, stroke: "none" }, g);
    el("text", { x: u.x + 10, y: u.y + 19, class: "unit-label" }, g).textContent = u.label;
    let row = 0;
    let textX = u.x + 10;
    if (u.tank) {
      // level gauge on the right side of the unit
      const gx = u.x + u.w - 30, gy = u.y + 28, gh = u.h - 40;
      el("rect", { x: gx, y: gy, width: 18, height: gh, class: "level-bg" }, g);
      el("rect", { x: gx + 1, y: gy + gh, width: 16, height: 0, class: "level-fill", id: `lv-${u.tank}`, "data-y": gy, "data-h": gh, "data-max": u.tankMax }, g);
      el("text", { x: textX, y: u.y + 42, class: "val-name" }, g).textContent = "Level";
      const t = el("text", { x: textX + 56, y: u.y + 42, class: "val" }, g);
      valueNodes[u.tank] = t;
      UNIT_OF[u.tank] = u.id;
      row = 1;
    }
    if (u.levels) {
      u.levels.forEach((tag, i) => {
        const gx = u.x + 104 + i * 29, gy = u.y + 30, gh = 66;
        el("rect", { x: gx, y: gy, width: 16, height: gh, class: "level-bg" }, g);
        el("rect", { x: gx + 1, y: gy + gh, width: 14, height: 0, class: "level-fill", id: `lv-${tag}`, "data-y": gy, "data-h": gh, "data-max": 100 }, g);
        el("text", { x: gx - 2, y: gy + gh + 14, class: "val-name" }, g).textContent = SHORT[tag];
        const t = el("text", { x: gx - 2, y: gy + gh + 26, class: "val" }, g);
        valueNodes[tag] = t;
        UNIT_OF[tag] = u.id;
      });
    }
    if (u.motor) {
      el("circle", { cx: u.x + u.w - 26, cy: u.y + 86, r: 17, class: "motor", fill: "#cfd6d4", id: "motor" }, g);
      el("text", { x: u.x + u.w - 26, y: u.y + 90, "text-anchor": "middle", class: "val-name", id: "motor-txt" }, g).textContent = "--";
      UNIT_OF[u.motor] = u.id;
      UNIT_OF["B-301_CMD"] = u.id;
    }
    for (const tag of u.tags) {
      const y = u.y + 42 + row * 19;
      el("text", { x: textX, y, class: "val-name" }, g).textContent = SHORT[tag] || tag;
      const t = el("text", { x: textX + (u.tank ? 56 : u.levels ? 44 : 62), y, class: "val" }, g);
      if (!valueNodes[tag]) valueNodes[tag] = t;
      else valueNodes[`${tag}#2`] = t;
      UNIT_OF[tag] = UNIT_OF[tag] || u.id;
      row++;
    }
  }
  el("text", { x: 1110, y: 333, class: "val-name", id: "consent-txt" }, svg).textContent = "";
}

function tagMeta(tag) {
  return META.tags.find((t) => t.name === tag) || { unit: "" };
}

function renderMimic() {
  if (!OVERVIEW) return;
  const v = OVERVIEW.values;
  const alarmByTag = {};
  for (const a of OVERVIEW.active_alarms) {
    if (a.cleared_ts) continue;
    const prev = alarmByTag[a.tag];
    if (!prev || PRIO_RANK[a.priority] > PRIO_RANK[prev.priority]) alarmByTag[a.tag] = a;
  }
  for (const [key, node] of Object.entries(valueNodes)) {
    const tag = key.split("#")[0];
    const m = tagMeta(tag);
    let txt = fmtVal(v[tag]);
    if (tag === "DS-900") txt = ["idle", "dispensing", "HOLD"][Math.round(v[tag] ?? 0)] || "--";
    else if (tag === "FQ-901") txt = String(Math.round(v[tag] ?? 0));
    else if (["LIT-901", "LIT-902", "LIT-903"].includes(tag)) txt = `${Math.round(v[tag] ?? 0)}%`;
    else if (m.unit && m.unit !== "") txt += ` ${m.unit === "degC" ? "°C" : m.unit}`;
    node.textContent = txt;
    const a = alarmByTag[tag];
    node.setAttribute("fill", a ? PRIO_COLOR[a.priority] : "#24302e");
    node.setAttribute("font-weight", a ? "700" : "400");
  }
  document.querySelectorAll("[id^=lv-]").forEach((r) => {
    const tag = r.id.slice(3);
    const val = v[tag] ?? 0;
    const max = +r.dataset.max, y0 = +r.dataset.y, h0 = +r.dataset.h;
    const h = Math.max(0, Math.min(1, val / max)) * (h0 - 2);
    r.setAttribute("y", y0 + h0 - 1 - h);
    r.setAttribute("height", h);
    r.setAttribute("fill", alarmByTag[tag] ? PRIO_COLOR[alarmByTag[tag].priority] : "#9fb5b9");
  });
  const running = (v["B-301_RUN"] ?? 0) > 0.5, cmd = (v["B-301_CMD"] ?? 0) > 0.5;
  $("motor").setAttribute("fill", running ? "#cfe3d7" : cmd ? "#f3c9c4" : "#cfd6d4");
  $("motor-txt").textContent = running ? "RUN" : cmd ? "FAIL" : "STOP";
  // unit borders take the highest priority among their tags' alarms
  const unitPrio = {};
  for (const a of Object.values(alarmByTag)) {
    const u = UNIT_OF[a.tag];
    if (u && (!unitPrio[u] || PRIO_RANK[a.priority] > PRIO_RANK[unitPrio[u]])) unitPrio[u] = a.priority;
  }
  for (const u of UNITS) {
    const box = $(`ab-${u.id}`);
    const p = unitPrio[u.id];
    box.setAttribute("stroke", p ? PRIO_COLOR[p] : "none");
  }
  const allOk = Object.values(OVERVIEW.consent).every(Boolean);
  const ct = $("consent-txt");
  ct.textContent = allOk ? "within consent" : "OUT OF CONSENT";
  ct.setAttribute("fill", allOk ? "#2e7d4f" : "#b3261e");
}

/* ------------------------------------------------------------------ header */
function renderHeader() {
  $("clock").textContent = fmtTs(OVERVIEW.plant_ts);
  $("mode").textContent = OVERVIEW.mode === "adaptive" ? "Adaptive" : "Fixed-rate";
  const col = OVERVIEW.collector || "";
  $("collector").textContent = col === "connected" ? `Modbus TCP ${META.modbus || ""}` : col.startsWith("disc") ? "Comms lost" : "Historian";
  $("collector").className = col.startsWith("disc") ? "bad" : "ok";
  const allOk = Object.values(OVERVIEW.consent).every(Boolean);
  $("consent").textContent = allOk ? "Within limits" : "Exceeded";
  $("consent").className = allOk ? "ok" : "bad";
  const faults = OVERVIEW.faults || [];
  $("activefaults").textContent = faults.length
    ? "Active training faults: " + faults.map((f) => `${f.description} (${f.minutes_left} min left)`).join("; ")
    : "No training fault active.";
}

/* ------------------------------------------------------------------ alarms */
function renderAlarms() {
  const rows = (alarmFilter === "active"
    ? ALARMS.filter((a) => a.suppressed === null && (a.cleared_ts === null || a.acked_ts === null))
    : ALARMS
  ).slice(0, 120);
  $("alarmrows").innerHTML = rows.length
    ? rows
        .map((a) => {
          const unack = a.acked_ts === null;
          const cls = [a.id === selectedAlarm ? "sel" : "", a.cleared_ts ? "cleared" : ""].join(" ");
          const state = a.suppressed ? `suppressed (${a.suppressed})` : a.state.replace("_", " ").toLowerCase();
          return `<tr class="${cls}" data-id="${a.id}">
            <td class="pbar"><div style="background:${PRIO_COLOR[a.priority]}" class="${unack && !a.cleared_ts ? "blink" : ""}"></div></td>
            <td>${hhmm(a.raised_ts)}</td><td>${esc(a.tag)}</td>
            <td>${esc(a.message)}<div class="layer">${esc(a.priority)} · ${esc(a.rule_id)}</div></td>
            <td class="layer">${esc(a.layer)}</td><td class="layer">${esc(state)}</td>
            <td><button class="btn primary" data-explain="${a.id}">Explain</button>
                ${unack ? `<button class="btn" data-ack="${a.id}">Ack</button>` : ""}</td></tr>`;
        })
        .join("")
    : `<tr><td colspan="7" class="muted" style="padding:10px">No active alarms.</td></tr>`;
}

/* ------------------------------------------------------------------ incidents */
async function refreshIncidents() {
  const [incs, k] = await Promise.all([api("/api/incidents?limit=15"), api("/api/kpis")]);
  $("kpis").textContent = `Shift ${k.shift}: ${k.alarms} alarms (${k.alarms_per_hour}/h) · acid ${k.acid_l} L · PAC ${k.coagulant_l} L · ${k.energy_kwh} kWh`;
  $("incidentlist").innerHTML = incs.length
    ? incs
        .map(
          (i) => `<div class="incident" style="border-left-color:${PRIO_COLOR[i.severity]}">
          <div class="head"><b>#${i.id} ${esc(i.title)}</b><span class="chip p-${i.severity}">${i.severity}</span></div>
          <div class="meta">${fmtTs(i.opened_ts)} → ${i.closed_ts ? hhmm(i.closed_ts) : "open"} · ${i.alert_ids.length} alarm(s) · area ${esc(i.area)}</div>
          <ul>${i.alarms
            .slice(0, 5)
            .map((a) => `<li>${hhmm(a.raised_ts)} ${esc(a.message)}${a.suppressed ? ` <span class="layer">(suppressed: ${a.suppressed})</span>` : ""}</li>`)
            .join("")}</ul></div>`
        )
        .join("")
    : `<p class="muted">No incidents yet.</p>`;
}

/* ------------------------------------------------------------------ trends */
function buildChips() {
  const pick = ["FIT-101", "AIT-101", "LIT-101", "AIT-201", "AIT-201B", "P-201_SP", "P-202_SP", "FIT-202", "LIT-202", "AIT-301", "SC-301", "JT-301", "AIT-401", "AIT-501", "AIT-502", "TT-501"];
  $("tagchips").innerHTML = pick
    .map((t) => {
      const on = trendTags.includes(t);
      const color = on ? SERIES[trendTags.indexOf(t) % SERIES.length] : "#c9d0ce";
      return `<button class="tagchip ${on ? "on" : ""}" data-tag="${t}" title="${esc(tagMeta(t).description)}"><i style="background:${color}"></i>${t}</button>`;
    })
    .join("");
}

async function refreshTrend() {
  if (!trendTags.length) return;
  const d = await api(`/api/trend?tags=${trendTags.join(",")}&hours=${trendHours}`);
  const datasets = trendTags.map((t, i) => ({
    label: `${t} (${tagMeta(t).unit || "-"})`,
    data: (d.series[t] || []).map(([ts, v]) => ({ x: ts * 1000, y: v })),
    borderColor: SERIES[i % SERIES.length],
    backgroundColor: SERIES[i % SERIES.length],
    borderWidth: 1.6,
    pointRadius: 0,
    yAxisID: `y${i}`,
    tension: 0,
  }));
  const scales = { x: { type: "linear", min: d.start * 1000, max: d.end * 1000, ticks: { callback: (v) => hhmm(v / 1000), maxTicksLimit: 8, color: "#5f6d6a" }, grid: { color: "#e1e6e4" } } };
  trendTags.forEach((t, i) => {
    scales[`y${i}`] = {
      position: i % 2 ? "right" : "left",
      display: i < 2,
      ticks: { color: SERIES[i % SERIES.length], maxTicksLimit: 6 },
      grid: { display: i === 0, color: "#e1e6e4" },
      title: { display: i < 2, text: trendTags[i], color: SERIES[i % SERIES.length] },
    };
  });
  if (!chart) {
    chart = new Chart($("chart"), {
      type: "line",
      data: { datasets },
      options: {
        animation: false,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 10, color: "#24302e" } },
          tooltip: { callbacks: { title: (items) => fmtTs(items[0].parsed.x / 1000) } },
        },
        scales,
      },
    });
  } else {
    chart.data.datasets = datasets;
    chart.options.scales = scales;
    chart.update("none");
  }
}

/* ------------------------------------------------------------------ assistant */
async function explain(id) {
  selectedAlarm = id;
  renderAlarms();
  const a = ALARMS.find((x) => x.id === id);
  $("asst-body").innerHTML = `<p class="muted">Reading the alarm, the last hour of history and the SOPs for <b>${esc(a ? a.message : "alarm " + id)}</b> ...</p>`;
  try {
    const ex = await api(`/api/assistant/${id}?llm=true`, { method: "POST" });
    renderExplanation(ex, a);
  } catch (e) {
    $("asst-body").innerHTML = `<p class="muted">Assistant unavailable: ${esc(e.message)}</p>`;
  }
}

function renderExplanation(ex, a) {
  const ans = ex.answer;
  const modeTxt = {
    template: "Retrieval + template (no model)",
    llm: "LLM answer, all guards passed",
    "llm-rejected": "LLM answer rejected by a guard; template shown",
  }[ex.mode];
  $("asst-mode").textContent = `${modeTxt}${ex.latency_s ? ` · ${ex.latency_s.toFixed(1)} s` : ""}${ex.cached ? " · cached" : ""}`;
  const guards = ex.guard
    ? Object.entries(ex.guard.checks)
        .map(([k, v]) => `<span class="guard ${v ? "pass" : "fail"}">${v ? "✓" : "✗"} ${k.replace("_", " ")}</span>`)
        .join("")
    : `<span class="guard pass">template: grounded by construction</span>`;
  $("asst-body").innerHTML = `
    <div class="muted">Alarm: <b>${esc(a ? a.message : "")}</b> (${esc(a ? a.tag : "")}) · retrieved: ${ex.retrieved.map((r) => esc(r.sop_id)).join(", ")}</div>
    <h3>What is happening</h3><div class="summary">${esc(ans.summary)}</div>
    <h3>Likely causes</h3><ul>${ans.likely_causes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
    <h3>Suggested checks</h3><ol>${ans.checks.map((c) => `<li>${esc(c)}</li>`).join("")}</ol>
    <h3>Shift-handover draft</h3><div class="handover">${esc(ans.handover)}</div>
    <h3>Sources and guards</h3>
    <div>${ans.citations.map((c) => `<span class="cite">${esc(c)}</span>`).join("")}</div>
    <div>${guards}</div>
    ${ex.fallback_reason ? `<div class="muted">Guard result: ${esc(ex.fallback_reason)}</div>` : ""}`;
}

/* ------------------------------------------------------------------ loops */
async function refreshFast() {
  try {
    [OVERVIEW, ALARMS] = await Promise.all([api("/api/overview"), api("/api/alarms?limit=200")]);
    renderHeader();
    renderMimic();
    renderAlarms();
  } catch (e) {
    $("collector").textContent = "API offline";
    $("collector").className = "bad";
  }
}

function wire() {
  $("alarmrows").addEventListener("click", async (e) => {
    const ex = e.target.closest("[data-explain]");
    const ack = e.target.closest("[data-ack]");
    if (ex) explain(+ex.dataset.explain);
    if (ack) {
      await api(`/api/alarms/${ack.dataset.ack}/ack`, { method: "POST" });
      refreshFast();
    }
  });
  $("alarmfilter").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    alarmFilter = b.dataset.f;
    document.querySelectorAll("#alarmfilter button").forEach((x) => x.classList.toggle("on", x === b));
    renderAlarms();
  });
  $("hours").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    trendHours = +b.dataset.h;
    document.querySelectorAll("#hours button").forEach((x) => x.classList.toggle("on", x === b));
    refreshTrend();
  });
  $("tagchips").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tag]");
    if (!b) return;
    const t = b.dataset.tag;
    trendTags = trendTags.includes(t) ? trendTags.filter((x) => x !== t) : [...trendTags, t].slice(-6);
    buildChips();
    refreshTrend();
  });
  $("faultbuttons").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-fault]");
    if (!b) return;
    const r = await api("/api/sim/inject", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: b.dataset.fault }) });
    $("activefaults").textContent = `Injected: ${r.injected} for ${r.duration_min} min`;
  });
}

async function main() {
  META = await api("/api/meta");
  buildMimic();
  buildChips();
  const faults = Object.entries(META.faults || {});
  $("faultbuttons").innerHTML = faults.length
    ? faults.map(([k, d]) => `<button class="btn" data-fault="${k}">${esc(d)}</button>`).join("")
    : `<span class="muted">Fault injection is available when the dashboard runs with the simulator (plant-ai demo).</span>`;
  wire();
  await refreshFast();
  await Promise.all([refreshTrend(), refreshIncidents()]);
  setInterval(refreshFast, 2000);
  setInterval(refreshTrend, 5000);
  setInterval(refreshIncidents, 5000);
}
main();
