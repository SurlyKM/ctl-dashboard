// Dashboard page — index.html

function renderReadiness(daily) {
  const latest = Object.entries(daily).sort().at(-1)?.[1] || {};
  const fmtSleep = s => s ? Math.floor(s/3600) + "h " + Math.round((s%3600)/60) + "m" : "–";
  const hrvStatus = (latest.hrv_status || "").toLowerCase();
  const hrvColor = hrvStatus === "balanced" ? "var(--fitness)" : hrvStatus === "low" ? "var(--danger)" : "var(--muted)";
  const stressVal = latest.stress_avg || 0;
  const stressSub = stressVal < 30 ? "low" : stressVal < 60 ? "moderate" : "high";
  const stressColor = stressVal < 30 ? "var(--fitness)" : stressVal < 60 ? "var(--fatigue)" : "var(--danger)";

  const render = (id, label, value, sub, subColor) => {
    const el = $(id);
    if (!el) return;
    el.innerHTML = '<span class="label">' + label + '</span>' +
      '<span class="num">' + value + '</span>' +
      (sub ? '<span class="delta" style="color:' + (subColor || "var(--muted)") + '">' + sub + '</span>' : '');
  };

  render("readiness-hrv",     "HRV",          latest.hrv_last_night ? latest.hrv_last_night + " ms" : "–", hrvStatus, hrvColor);
  render("readiness-sleep",   "Sleep",         fmtSleep(latest.sleep_s), latest.sleep_score ? "score " + latest.sleep_score : "");
  render("readiness-battery", "Body battery",  latest.body_battery_high ?? "–", "");
  render("readiness-stress",  "Stress avg",    latest.stress_avg ?? "–", stressSub, stressColor);
}

function renderStats(metrics, activities, daily, ts) {
  const cur = metrics.current || {};
  const s = metrics.series;
  const [, tsbBg, tsbFg] = tsbZone(cur.tsb ?? 0);

  $("tsb-card").innerHTML =
    '<span class="label">Form (TSB)</span>' +
    '<span class="num" style="color:' + tsbFg + '">' + (cur.tsb > 0 ? "+" : "") + (cur.tsb ?? 0) + '</span>' +
    '<span class="delta" style="color:' + tsbFg + '">' + tsbZone(cur.tsb ?? 0)[0].toLowerCase() + '</span>';

  $("ctl").textContent = Math.round(cur.ctl ?? 0);
  $("atl").textContent = Math.round(cur.atl ?? 0);

  const weekAgo = s[Math.max(0, s.length - 8)];
  if (weekAgo) {
    const d = Math.round(cur.ctl - weekAgo.ctl);
    $("ctl-delta").textContent = (d >= 0 ? "+" : "") + d + " wk";
    $("ctl-delta").style.color = d < 0 ? css("--fatigue") : css("--fitness");
  }

  // Get Monday's date using local timezone offset to avoid UTC rollback
  const now = new Date();
  const localOffset = now.getTimezoneOffset() * 60000;
  const localNow = new Date(now.getTime() - localOffset);
  const localDay = localNow.getUTCDay(); // 0=Sun,1=Mon...
  const daysFromMonday = (localDay + 6) % 7;
  const mondayLocal = new Date(localNow);
  mondayLocal.setUTCDate(localNow.getUTCDate() - daysFromMonday);
  const mondayStr = mondayLocal.toISOString().slice(0, 10);
  const hrs = activities.filter(a => a.start && a.start.slice(0, 10) >= mondayStr)
    .reduce((t, a) => t + (a.duration_s || 0) / 3600, 0);
  $("week-hours").textContent = hrs.toFixed(1) + " h";

  // Resting HR from latest daily entry
  const latestDaily = Object.entries(daily).sort().at(-1)?.[1] || {};
  const rhr = latestDaily.resting_hr;
  if (rhr) {
    $("rhr-stat").textContent = rhr + " bpm";
    const rhrVals = Object.entries(daily).sort().slice(-7).map(([,v]) => v.resting_hr).filter(Boolean);
    if (rhrVals.length >= 3) {
      const first = rhrVals.slice(0, Math.floor(rhrVals.length/2)).reduce((a,b)=>a+b,0) / Math.floor(rhrVals.length/2);
      const last  = rhrVals.slice(Math.ceil(rhrVals.length/2)).reduce((a,b)=>a+b,0) / (rhrVals.length - Math.ceil(rhrVals.length/2));
      const diff = last - first;
      const trendEl = $("rhr-sub");
      if (trendEl) {
        trendEl.textContent = diff > 1.5 ? "rising" : diff < -1.5 ? "falling" : "stable";
        trendEl.style.color = diff > 1.5 ? css("--fatigue") : diff < -1.5 ? css("--fitness") : css("--muted");
      }
    }
  }
  // VO2 max
  if (ts) {
    const v = ts.vo2max_cycling || ts.vo2max_generic;
    if (v) {
      $("vo2-stat").textContent = Math.round(v);
      if (ts.fitness_age) $("vo2-sub").textContent = "age " + ts.fitness_age;
    }
  }
}

let _chartInstance = null;
let _chartMetrics = null;
let _chartPlan = null;

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
  const series = metrics.series.slice(-84);
  const todayIdx = new Date().getDay() === 0 ? 6 : new Date().getDay() - 1;
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
    const [metrics, activities, daily, plan, meta, ts] = await Promise.all([
      load("metrics"), load("activities"), load("daily"),
      load("plan").catch(() => null),
      load("meta").catch(() => null),
      load("training_status").catch(() => null),
    ]);
    renderSynced(meta);
    renderReadiness(daily);
    renderStats(metrics, activities, daily, ts);
    _chartMetrics = metrics;
    _chartPlan = plan;
    buildChart();
    document.addEventListener("themechange", buildChart);
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
