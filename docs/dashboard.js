// Dashboard page — index.html

// ---------------------------------------------------------------------------
// Stat cards: mini sparkline on every card, tap to expand in place
// ---------------------------------------------------------------------------

const WINDOW_KEY = "statWindow";
let _statWindow = parseInt(localStorage.getItem(WINDOW_KEY), 10) === 30 ? 30 : 7;
let _statCtx = null;

function isoLocal(d) {
  return d.getFullYear() + "-" +
    String(d.getMonth() + 1).padStart(2, "0") + "-" +
    String(d.getDate()).padStart(2, "0");
}

function lastNDates(n) {
  const today = new Date();
  const out = [];
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    out.push(isoLocal(d));
  }
  return out;
}

function fromDaily(daily, field, days, scale) {
  return lastNDates(days).map(d => {
    const raw = daily[d] ? daily[d][field] : null;
    return { date: d, v: raw == null ? null : (scale ? raw * scale : raw) };
  });
}

function fromSeries(series, field, days) {
  const map = {};
  (series || []).forEach(p => { map[p.date] = p[field]; });
  return lastNDates(days).map(d => ({ date: d, v: map[d] == null ? null : map[d] }));
}

function fromObject(obj, days) {
  return lastNDates(days).map(d => ({ date: d, v: obj && obj[d] != null ? obj[d] : null }));
}

function dailyHours(activities, days) {
  const map = {};
  (activities || []).forEach(a => {
    if (!a.start) return;
    const k = a.start.slice(0, 10);
    map[k] = (map[k] || 0) + (a.duration_s || 0) / 3600;
  });
  return lastNDates(days).map(d => ({ date: d, v: map[d] || 0 }));
}

function linSlope(points) {
  const xs = [], ys = [];
  points.forEach((p, i) => { if (p.v != null) { xs.push(i); ys.push(p.v); } });
  const n = xs.length;
  if (n < 3) return null;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) { num += (xs[i] - mx) * (ys[i] - my); den += (xs[i] - mx) ** 2; }
  return den ? num / den : null;
}

// ---------------------------------------------------------------------------
// Sparkline renderer. Fixed viewBox stretched to fit, strokes stay crisp via
// non-scaling-stroke so the same code works in a 120px card and a 700px row.
// ---------------------------------------------------------------------------

function sparkSvg(points, opts) {
  const o = Object.assign(
    { h: 24, type: "line", color: "var(--fitness)", sw: 1.5, fill: true, dot: true, cls: "spark" },
    opts || {});
  const vals = points.map(p => p.v).filter(v => v != null);
  if (vals.length < 2) return "";

  const W = 100, H = o.h;
  let mn = Math.min(...vals), mx = Math.max(...vals);
  if (o.type === "bar") mn = Math.min(0, mn);
  if (mx === mn) mx = mn + (Math.abs(mn) * 0.05 || 1);
  const pad = (mx - mn) * 0.15;
  const lo = o.type === "bar" ? mn : mn - pad;
  const hi = mx + pad;
  const X = i => points.length === 1 ? W / 2 : (i / (points.length - 1)) * W;
  const inset = o.type === "bar" ? 0.5 : 2.5;   // keeps the end marker off the edge
  const Y = v => H - ((v - lo) / (hi - lo)) * (H - inset * 2) - inset;

  let body = "";

  if (o.type === "bar") {
    const slot = W / points.length;
    const bw = slot * 0.6;
    const base = Y(lo);
    body = points.map((p, i) => {
      if (p.v == null) return "";
      const x = slot * i + (slot - bw) / 2;
      const y = Y(p.v);
      const hgt = Math.max(0.5, base - y);
      return '<rect x="' + x.toFixed(2) + '" y="' + y.toFixed(2) +
        '" width="' + bw.toFixed(2) + '" height="' + hgt.toFixed(2) +
        '" fill="' + o.color + '" opacity="0.55"/>';
    }).join("");
  } else {
    const segs = [];
    let run = [];
    points.forEach((p, i) => {
      if (p.v == null) { if (run.length) segs.push(run); run = []; }
      else run.push([X(i), Y(p.v)]);
    });
    if (run.length) segs.push(run);

    if (o.fill) {
      const longest = segs.reduce((a, b) => b.length > a.length ? b : a, []);
      if (longest.length > 1) {
        const d = "M" + longest.map(pt => pt[0].toFixed(2) + "," + pt[1].toFixed(2)).join(" L") +
          " L" + longest[longest.length - 1][0].toFixed(2) + "," + H +
          " L" + longest[0][0].toFixed(2) + "," + H + " Z";
        body += '<path d="' + d + '" fill="' + o.color + '" opacity="0.09"/>';
      }
    }

    body += segs.filter(s => s.length > 1).map(s =>
      '<polyline points="' + s.map(pt => pt[0].toFixed(2) + "," + pt[1].toFixed(2)).join(" ") +
      '" fill="none" stroke="' + o.color + '" stroke-width="' + o.sw +
      '" stroke-linejoin="round" stroke-linecap="round"' +
      ' vector-effect="non-scaling-stroke" opacity="0.85"/>').join("");

    if (o.dot) {
      let li = -1;
      points.forEach((p, i) => { if (p.v != null) li = i; });
      if (li >= 0) {
        const x = X(li).toFixed(2), y = Y(points[li].v).toFixed(2);
        body += '<line x1="' + x + '" y1="' + y + '" x2="' + x + '" y2="' + y +
          '" stroke="' + o.color + '" stroke-width="' + (o.sw * 2.4).toFixed(1) +
          '" stroke-linecap="round" vector-effect="non-scaling-stroke"/>';
      }
    }
  }

  return '<svg class="' + o.cls + '" viewBox="0 0 ' + W + ' ' + H +
    '" preserveAspectRatio="none" aria-hidden="true">' + body + '</svg>';
}

// ---------------------------------------------------------------------------
// Card definitions
// ---------------------------------------------------------------------------

const DAY_INITIALS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

const fmtInt   = v => String(Math.round(v));
const fmtOne   = v => v.toFixed(1);
const fmtMs    = v => Math.round(v) + " ms";
const fmtBpm   = v => Math.round(v) + " bpm";
const fmtHrs   = v => v.toFixed(1) + " h";
const fmtSleep = v => Math.floor(v) + "h " + Math.round((v % 1) * 60) + "m";

function cardDefs(ctx) {
  const { metrics, activities, daily, ts, vo2hist } = ctx;
  const d = _statWindow;
  const cur = metrics.current || {};
  const entries = Object.entries(daily).sort();
  const latest = entries.length ? entries[entries.length - 1][1] : {};

  // Form zone colour
  const [tsbLabel, , tsbFg] = tsbZone(cur.tsb ?? 0);

  // HRV status
  const hrvStatus = (latest.hrv_status || "").toLowerCase();
  const hrvColor = hrvStatus === "balanced" ? "var(--fitness)"
    : hrvStatus === "low" ? "var(--danger)" : "var(--muted)";

  // Stress banding
  const stress = latest.stress_avg || 0;
  const stressSub = stress < 30 ? "low" : stress < 60 ? "moderate" : "high";
  const stressColor = stress < 30 ? "var(--fitness)" : stress < 60 ? "var(--fatigue)" : "var(--danger)";

  // Week hours, Monday based
  const now = new Date();
  const monday = new Date(now);
  monday.setDate(now.getDate() - ((now.getDay() + 6) % 7));
  const mondayStr = isoLocal(monday);
  const weekHrs = (activities || [])
    .filter(a => a.start && a.start.slice(0, 10) >= mondayStr)
    .reduce((t, a) => t + (a.duration_s || 0) / 3600, 0);

  // CTL week on week
  const s = metrics.series || [];
  const weekAgo = s[Math.max(0, s.length - 8)];
  const ctlDelta = weekAgo ? Math.round((cur.ctl || 0) - weekAgo.ctl) : null;

  // Resting HR trend from a least squares fit over the window
  const rhrSeries = fromDaily(daily, "resting_hr", d);
  const rhrSlope = linSlope(rhrSeries);
  const rhrTrend = rhrSlope == null ? ""
    : rhrSlope > 0.3 ? "rising" : rhrSlope < -0.3 ? "falling" : "stable";
  const rhrColor = rhrTrend === "rising" ? "var(--fatigue)"
    : rhrTrend === "falling" ? "var(--fitness)" : "var(--muted)";

  const vo2 = ts ? (ts.vo2max_cycling || ts.vo2max_generic) : null;

  return [
    { key: "hrv", label: "HRV",
      value: latest.hrv_last_night ? latest.hrv_last_night + " ms" : "–",
      sub: hrvStatus, subColor: hrvColor,
      series: fromDaily(daily, "hrv_last_night", d),
      fmt: fmtMs, fmtShort: fmtInt },

    { key: "sleep", label: "Sleep",
      value: latest.sleep_s ? fmtSleep(latest.sleep_s / 3600) : "–",
      sub: latest.sleep_score ? "score " + latest.sleep_score : "",
      series: fromDaily(daily, "sleep_s", d, 1 / 3600),
      fmt: fmtSleep, fmtShort: v => v.toFixed(1),
      fmtDelta: v => (v > 0 ? "+" : "-") + Math.round(Math.abs(v) * 60) + " m" },

    { key: "battery", label: "Body battery",
      value: latest.body_battery_high ?? "–", sub: "",
      series: fromDaily(daily, "body_battery_high", d),
      fmt: fmtInt, fmtShort: fmtInt },

    { key: "stress", label: "Stress avg",
      value: latest.stress_avg ?? "–", sub: stressSub, subColor: stressColor,
      series: fromDaily(daily, "stress_avg", d),
      fmt: fmtInt, fmtShort: fmtInt, invert: true },

    { key: "tsb", label: "Form (TSB)",
      value: (cur.tsb > 0 ? "+" : "") + (cur.tsb ?? 0), valueColor: tsbFg,
      sub: tsbLabel.toLowerCase(), subColor: tsbFg,
      series: fromSeries(s, "tsb", d),
      fmt: fmtOne, fmtShort: fmtInt },

    { key: "ctl", label: "Fitness (CTL)",
      value: Math.round(cur.ctl ?? 0),
      sub: ctlDelta == null ? "" : (ctlDelta >= 0 ? "+" : "") + ctlDelta + " wk",
      subColor: ctlDelta != null && ctlDelta < 0 ? "var(--fatigue)" : "var(--fitness)",
      series: fromSeries(s, "ctl", d),
      fmt: fmtOne, fmtShort: fmtInt },

    { key: "atl", label: "Fatigue (ATL)",
      value: Math.round(cur.atl ?? 0), sub: "",
      series: fromSeries(s, "atl", d),
      fmt: fmtOne, fmtShort: fmtInt, invert: true },

    { key: "week", label: "This week",
      value: weekHrs.toFixed(1) + " h", sub: "daily hours",
      series: dailyHours(activities, d), chart: "bar",
      fmt: fmtHrs, fmtShort: v => v ? v.toFixed(1) : "–" },

    { key: "rhr", label: "Resting HR",
      value: latest.resting_hr ? latest.resting_hr + " bpm" : "–",
      sub: rhrTrend, subColor: rhrColor,
      series: rhrSeries,
      fmt: fmtBpm, fmtShort: fmtInt, invert: true },

    { key: "vo2", label: "VO2 max",
      value: vo2 ? Math.round(vo2) : "–",
      sub: ts && ts.fitness_age ? "age " + ts.fitness_age : "",
      series: fromObject(vo2hist, d),
      fmt: fmtOne, fmtShort: fmtOne,
      empty: "Logging starts today. The trend appears once a few days are stored." },
  ];
}

// ---------------------------------------------------------------------------
// Card rendering
// ---------------------------------------------------------------------------

function winPills() {
  return '<div class="win-pills">' + [7, 30].map(n =>
    '<button class="win-pill' + (_statWindow === n ? " active" : "") +
    '" data-win="' + n + '">' + n + 'd</button>').join("") + '</div>';
}

function dstat(label, value, color) {
  return '<div class="detail-stat"><span class="detail-stat-lbl">' + label + '</span>' +
    '<span class="detail-stat-val"' + (color ? ' style="color:' + color + '"' : '') + '>' +
    value + '</span></div>';
}

function dayStrip(points, c) {
  return '<div class="day-strip">' + points.map(p => {
    const dt = new Date(p.date + "T00:00");
    return '<div class="day-cell">' +
      '<span class="day-cell-lbl">' + DAY_INITIALS[dt.getDay()] + '</span>' +
      '<span class="day-cell-val">' + (p.v == null ? "–" : c.fmtShort(p.v)) + '</span>' +
      '</div>';
  }).join("") + '</div>';
}

function detailHtml(c) {
  const vals = c.series.map(p => p.v).filter(v => v != null);
  if (vals.length < 2) {
    return '<div class="stat-detail"><p class="spark-empty">' +
      (c.empty || "Not enough history stored yet.") + '</p>' + winPills() + '</div>';
  }
  const first = vals[0], last = vals[vals.length - 1];
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  const change = last - first;
  const fmtD = c.fmtDelta || (v => (v > 0 ? "+" : "") + c.fmt(v));
  const flat = Math.abs(change) < 1e-9;
  const good = c.invert ? change < 0 : change > 0;
  const changeColor = flat ? "var(--muted)" : good ? "var(--fitness)" : "var(--fatigue)";

  return '<div class="stat-detail">' +
    sparkSvg(c.series, { h: 44, type: c.chart || "line", sw: 2, cls: "spark-lg" }) +
    '<div class="detail-stats">' +
      dstat("Low", c.fmt(Math.min(...vals))) +
      dstat("Avg", c.fmt(avg)) +
      dstat("High", c.fmt(Math.max(...vals))) +
      dstat("Change", fmtD(change), changeColor) +
    '</div>' +
    (_statWindow === 7 ? dayStrip(c.series, c) : "") +
    winPills() +
    '</div>';
}

function cardHtml(c) {
  const mini = sparkSvg(c.series, { h: 22, type: c.chart || "line", sw: 1.5, dot: true });
  return '<span class="label">' + c.label + '</span>' +
    '<span class="num"' + (c.valueColor ? ' style="color:' + c.valueColor + '"' : '') + '>' +
      c.value + '</span>' +
    (c.sub ? '<span class="delta" style="color:' + (c.subColor || "var(--muted)") + '">' +
      c.sub + '</span>' : '<span class="delta"></span>') +
    (mini || '<div class="spark-blank"></div>') +
    detailHtml(c);
}

function renderStatCards() {
  if (!_statCtx) return;
  cardDefs(_statCtx).forEach(c => {
    const el = $("card-" + c.key);
    if (el) el.innerHTML = cardHtml(c);
  });
}

function setStatWindow(n) {
  _statWindow = n === 30 ? 30 : 7;
  localStorage.setItem(WINDOW_KEY, String(_statWindow));
  renderStatCards();
}

function wireStatCards() {
  const grid = document.querySelector(".stats");
  if (!grid) return;

  grid.addEventListener("click", e => {
    const pill = e.target.closest(".win-pill");
    if (pill) {
      e.stopPropagation();
      setStatWindow(parseInt(pill.dataset.win, 10));
      return;
    }
    const card = e.target.closest(".stat");
    if (!card) return;
    const wasOpen = card.classList.contains("expanded");
    grid.querySelectorAll(".stat.expanded").forEach(c => c.classList.remove("expanded"));
    if (!wasOpen) card.classList.add("expanded");
  });

  grid.addEventListener("keydown", e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const card = e.target.closest(".stat");
    if (!card || e.target.closest(".win-pill")) return;
    e.preventDefault();
    card.click();
  });
}

// ---------------------------------------------------------------------------
// Load chart
// ---------------------------------------------------------------------------

let _chartInstance = null;
let _chartMetrics = null;
let _chartPlan = null;
let _chartDays = 84;

// Chart colour palettes keyed by theme + mode — no CSS variable lookups at render time
function chartColors() {
  const theme = document.body.getAttribute("data-theme") || "green";
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const palettes = {
    green:  { fitness: dark ? "#5cb86a" : "#2d6a35", fatigue: dark ? "#e09a40" : "#b85c00", bar: dark ? "rgba(92,184,106,0.18)" : "rgba(45,106,53,0.15)", grid: dark ? "#243d27" : "#dceadd", muted: dark ? "#5a7a5c" : "#888888" },
    slate:  { fitness: dark ? "#60a5fa" : "#1d4ed8", fatigue: dark ? "#fb923c" : "#b45309", bar: dark ? "rgba(96,165,250,0.18)" : "rgba(29,78,216,0.12)", grid: dark ? "#252d42" : "#e2e8f0", muted: dark ? "#6b7599" : "#9ca3af" },
    warm:   { fitness: dark ? "#fbbf24" : "#b45309", fatigue: dark ? "#fb923c" : "#92400e", bar: dark ? "rgba(251,191,36,0.18)" : "rgba(180,83,9,0.12)",  grid: dark ? "#3a3330" : "#e8e4de", muted: dark ? "#857770" : "#a8a29e" },
    mono:   { fitness: dark ? "#a3a3a3" : "#374151", fatigue: dark ? "#737373" : "#6b7280", bar: dark ? "rgba(163,163,163,0.18)" : "rgba(55,65,81,0.12)",  grid: dark ? "#2e2e2e" : "#e5e7eb", muted: dark ? "#737373" : "#9ca3af" },
  };
  return palettes[theme] || palettes.green;
}

function buildChart() {
  if (!_chartMetrics) return;
  if (_chartInstance) { _chartInstance.destroy(); _chartInstance = null; }
  const wrap = document.querySelector(".chart-wrap");
  if (wrap) {
    const c = wrap.querySelector("canvas");
    if (c) c.remove();
    const fresh = document.createElement("canvas");
    fresh.id = "loadChart";
    wrap.appendChild(fresh);
  }
  const metrics = _chartMetrics, plan = _chartPlan;
  const series = metrics.series.slice(-_chartDays);
  const _localDay = new Date().getDay();
  const todayIdx = _localDay === 0 ? 6 : _localDay - 1;
  const todayPlan = plan?.days?.[todayIdx];
  const todayLabel = todayPlan
    ? "Today: " + todayPlan.session + (todayPlan.duration_min ? " · " + todayPlan.duration_min + " min" : "")
    : null;
  const [, , intFg] = todayPlan ? (INTENSITY_PILL[todayPlan.intensity] || INTENSITY_PILL.easy) : ["","",""];
  if (todayLabel) {
    const label = $("chart-today-label");
    if (label) { label.textContent = todayLabel; label.style.color = intFg || "#888"; }
  }
  const col = chartColors();
  const chartEl = $("loadChart");
  _chartInstance = new Chart(chartEl, {
    data: {
      labels: series.map(p => p.date.slice(5)),
      datasets: [
        { type: "bar",  label: "Daily load", data: series.map(p => p.load),
          backgroundColor: col.bar, order: 3, yAxisID: "y1" },
        { type: "line", label: "Fitness", data: series.map(p => p.ctl),
          borderColor: col.fitness, borderWidth: 2, pointRadius: 0, tension: 0.35, order: 1 },
        { type: "line", label: "Fatigue", data: series.map(p => p.atl),
          borderColor: col.fatigue, borderWidth: 1.5, borderDash: [5,4],
          pointRadius: 0, tension: 0.35, order: 2 },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { boxWidth: 16, boxHeight: 2, font: { size: 11 }, color: col.muted } } },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 8, color: col.muted, font: { size: 10 } } },
        y: { grid: { color: col.grid }, ticks: { color: col.muted, font: { size: 10 } } },
        y1: { position: "right", display: false },
      },
    },
  });
}

function renderMix(activities) {
  const cutoff = new Date(Date.now() - 28 * 864e5);
  const hours = {};
  activities.forEach(a => {
    if (a.start && new Date(a.start) >= cutoff)
      hours[a.sport || "other"] = (hours[a.sport || "other"] || 0) + (a.duration_s || 0) / 3600;
  });
  const entries = Object.entries(hours).sort((a,b) => b[1] - a[1]);
  const max = entries[0]?.[1] || 1;
  $("mix").innerHTML = entries.map(([sport, h]) =>
    '<div class="mix-row">' +
      '<span class="mix-label">' + (SPORT_LABELS[sport] || sport) + '</span>' +
      '<div class="mix-bar-wrap"><div class="mix-bar" style="width:' + ((h/max)*100) + '%"></div></div>' +
      '<span class="mix-hours">' + h.toFixed(1) + ' h</span>' +
    '</div>'
  ).join("");
}

function renderVO2(ts, activities) {
  const el = $("vo2-panel");
  if (!el || !ts) return;
  const v = ts.vo2max_cycling || ts.vo2max_generic;
  if (!v) { el.innerHTML = '<span class="muted">No data</span>'; return; }
  el.innerHTML =
    '<div class="vo2-row">' +
      '<div><div class="vo2-num">' + v.toFixed(1) + '</div>' +
      '<div class="vo2-sub">' + (ts.fitness_age ? "Fitness age " + ts.fitness_age : "") + '</div></div>' +
      '<div class="vo2-trend"><div class="vo2-trend-label">Trend (from activities)</div>' +
      '<svg id="vo2svg" viewBox="0 0 120 38" style="width:100%;height:38px;overflow:visible;display:block"></svg>' +
      '<div style="display:flex;justify-content:space-between;font-size:9px;color:var(--muted);margin-top:2px"><span>12 wk ago</span><span>now</span></div>' +
      '</div></div>';
  const svg = document.getElementById("vo2svg");
  const byWeek = {};
  activities.forEach(a => {
    if (!a.start || !a.avg_hr || !a.max_hr) return;
    const wk = Math.floor((Date.now() - new Date(a.start)) / (7*864e5));
    if (wk > 12) return;
    const hrr = (a.avg_hr - 50) / (a.max_hr - 50);
    const effort = Math.max(0.3, Math.min(1, hrr));
    (byWeek[wk] = byWeek[wk] || []).push((a.training_load || 0) / effort);
  });
  const vals = Array.from({length:13}, (_,i) => 12-i).map(w =>
    byWeek[w] ? byWeek[w].reduce((a,b)=>a+b,0)/byWeek[w].length : null);
  const filled = vals.map((v,i) => v ?? vals.slice(0,i).reverse().find(x=>x) ?? 0);
  const mn = Math.min(...filled), mx = Math.max(...filled) || 1;
  const pts = filled.map((v,i) => ((i/12)*120) + "," + (38 - ((v-mn)/(mx-mn||1))*30 - 4)).join(" ");
  const last = filled[filled.length-1];
  const ly = 38 - ((last-mn)/(mx-mn||1))*30 - 4;
  svg.innerHTML =
    '<polyline points="' + pts + '" fill="none" stroke="var(--line2)" stroke-width="1.5" stroke-linejoin="round"/>' +
    '<polyline points="' + pts + '" fill="none" stroke="var(--fitness)" stroke-width="2" stroke-linejoin="round" opacity="0.8"/>' +
    '<circle cx="120" cy="' + ly + '" r="3" fill="var(--fitness)"/>';
}

async function main() {
  try {
    const [metrics, activities, daily, plan, meta, ts, vo2hist] = await Promise.all([
      load("metrics"), load("activities"), load("daily"),
      load("plan").catch(() => null),
      load("meta").catch(() => null),
      load("training_status").catch(() => null),
      load("vo2_history").catch(() => null),
    ]);
    renderSynced(meta);
    _statCtx = { metrics, activities, daily, ts, vo2hist };
    renderStatCards();
    wireStatCards();
    _chartMetrics = metrics;
    _chartPlan = plan;
    buildChart();
    document.addEventListener("themechange", buildChart);
    document.querySelectorAll(".range-btn").forEach(function(btn) {
      btn.addEventListener("click", function() {
        _chartDays = parseInt(btn.dataset.days);
        document.querySelectorAll(".range-btn").forEach(function(b) {
          b.classList.toggle("active", b === btn);
        });
        var title = $("chart-title");
        if (title) title.textContent = "Training load, " + (_chartDays === 84 ? "12 weeks" : "6 months");
        buildChart();
      });
    });
    renderMix(activities);
    renderVO2(ts, activities);
    renderGarminStatus(ts);
    renderLoadBalance(ts);
  } catch (e) {
    document.body.insertAdjacentHTML("beforeend",
      '<p class="muted" style="padding:20px">No data yet. Run the sync workflow first. (' + e.message + ')</p>');
  }
}
main();
