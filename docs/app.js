// All data comes from static JSON committed by the GitHub Actions pipeline.
const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const SPORT_LABELS = {
  cycling: "Cycling", mtb: "MTB", swim: "Swim", gym: "Gym",
  walk_hike: "Walk/hike", yoga: "Yoga", rest: "Rest", other: "Other",
};

async function load(name) {
  const r = await fetch(`data/${name}.json`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${name}.json ${r.status}`);
  return r.json();
}

function tsbZone(tsb) {
  if (tsb < -25) return ["Overreached", "var(--danger-bg)",   "var(--danger)"];
  if (tsb < -10) return ["Fatigued",    "#fff4e5",            "var(--fatigue)"];
  if (tsb <   5) return ["Neutral",     "var(--line)",        "var(--ink2)"];
  if (tsb <  20) return ["Fresh",       "var(--fitness-bg)",  "var(--fitness)"];
  return                ["Detraining",  "var(--line)",        "var(--muted)"];
}

function renderGauge(tsb) {
  $("tsb-value").textContent = tsb > 0 ? "+" + tsb : tsb;
  const [zone, bg, fg] = tsbZone(tsb);
  $("tsb-zone").textContent = zone;
  $("tsb-zone").style.background = bg;
  $("tsb-zone").style.color = fg;
  const pct = Math.max(0, Math.min(100, ((tsb + 40) / 70) * 100));
  $("gauge-marker").style.left = "calc(" + pct + "% - 1px)";
}

function renderStats(metrics, activities, daily) {
  const s = metrics.series;
  const cur = metrics.current || {};
  $("ctl").textContent = Math.round(cur.ctl ?? 0);
  $("atl").textContent = Math.round(cur.atl ?? 0);
  const weekAgo = s[Math.max(0, s.length - 8)];
  if (weekAgo) {
    const d = Math.round(cur.ctl - weekAgo.ctl);
    $("ctl-delta").textContent = d >= 0 ? `+${d} this week` : `${d} this week`;
    if (d < 0) $("ctl-delta").style.color = css("--fatigue");
  }
  // Hours in the current Monday-based week
  const now = new Date();
  const monday = new Date(now);
  monday.setDate(now.getDate() - ((now.getDay() + 6) % 7));
  monday.setHours(0, 0, 0, 0);
  const hrs = activities
    .filter((a) => a.start && new Date(a.start) >= monday)
    .reduce((t, a) => t + (a.duration_s || 0) / 3600, 0);
  $("week-hours").textContent = `${hrs.toFixed(1)} h`;
  // 7-day average sleep score
  const scores = Object.entries(daily).sort().slice(-7)
    .map(([, v]) => v.sleep_score).filter(Boolean);
  $("sleep7").textContent = scores.length
    ? Math.round(scores.reduce((a, b) => a + b) / scores.length) : "–";
}

function renderChart(metrics) {
  const series = metrics.series.slice(-84);
  new Chart($("loadChart"), {
    data: {
      labels: series.map((p) => p.date.slice(5)),
      datasets: [
        { type: "bar", label: "Daily load", data: series.map((p) => p.load),
          backgroundColor: css("--line"), order: 3, yAxisID: "y1" },
        { type: "line", label: "Fitness", data: series.map((p) => p.ctl),
          borderColor: css("--fitness"), borderWidth: 2, pointRadius: 0,
          tension: 0.35, order: 1 },
        { type: "line", label: "Fatigue", data: series.map((p) => p.atl),
          borderColor: css("--fatigue"), borderWidth: 1.5, borderDash: [5, 4],
          pointRadius: 0, tension: 0.35, order: 2 },
      ],
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { boxWidth: 16, boxHeight: 2, font: { size: 11 }, color: css("--muted") } } },
      scales: {
        x: { grid: { display: false },
             ticks: { maxTicksLimit: 8, color: css("--muted"), font: { size: 10 } } },
        y: { grid: { color: css("--line") },
             ticks: { color: css("--muted"), font: { size: 10 } } },
        y1: { position: "right", display: false },
      },
    },
  });
}

function renderMix(activities) {
  const cutoff = new Date(Date.now() - 28 * 864e5);
  const hours = {};
  activities.forEach((a) => {
    if (a.start && new Date(a.start) >= cutoff)
      hours[a.sport || "other"] = (hours[a.sport || "other"] || 0) + (a.duration_s || 0) / 3600;
  });
  const entries = Object.entries(hours).sort((a, b) => b[1] - a[1]);
  const max = entries[0]?.[1] || 1;
  $("mix").innerHTML = entries.map(([sport, h]) => `
    <div class="mix-row">
      <span class="mix-label">${SPORT_LABELS[sport] || sport}</span>
      <div class="mix-bar-wrap"><div class="mix-bar" style="width:${(h / max) * 100}%"></div></div>
      <span class="mix-hours">${h.toFixed(1)} h</span>
    </div>`).join("");
}

function renderRecovery(daily) {
  const days = Object.entries(daily).sort();
  const latest = days.at(-1)?.[1] || {};
  const fmtSleep = (s) => s ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} m` : "–";
  const rows = [
    ["HRV last night", latest.hrv_last_night ? `${latest.hrv_last_night} ms` : "–"],
    ["HRV status", (latest.hrv_status || "–").toLowerCase()],
    ["Resting HR", latest.resting_hr ? `${latest.resting_hr} bpm` : "–"],
    ["Sleep last night", fmtSleep(latest.sleep_s)],
    ["Body battery high", latest.body_battery_high ?? "–"],
    ["Stress avg", latest.stress_avg ?? "–"],
  ];
  $("recovery").innerHTML = rows
    .map(([k, v]) => `<tr><td>${k}</td><td class="mono">${v}</td></tr>`).join("");
}

function renderPlan(plan, activities) {
  if (!plan?.days) return;
  $("plan-generated").textContent =
    `generated ${new Date(plan.generated_at).toLocaleDateString()}`;
  $("coach-says").textContent = plan.coach_says || "";

  const weekStart = new Date(plan.week_start + "T00:00");
  const todayStr = new Date().toDateString();
  // Which sports actually happened on each planned day
  const doneByDate = {};
  activities.forEach((a) => {
    if (a.start) (doneByDate[a.start.slice(0, 10)] ||= new Set()).add(a.sport);
  });

  $("plan").innerHTML = plan.days.map((d, i) => {
    const date = new Date(weekStart); date.setDate(weekStart.getDate() + i);
    const dateStr = date.toISOString().slice(0, 10);
    const isToday = date.toDateString() === todayStr;
    const done = doneByDate[dateStr]?.has(d.sport) ||
      (d.sport === "rest" && date < new Date() && !doneByDate[dateStr]);
    const status = done && date <= new Date() ? `<span class="st-done">done</span>`
      : isToday ? `<span class="st-today">today</span>` : "";
    return `<tr class="${isToday ? "today" : ""}">
      <td class="day">${d.day}</td>
      <td><span class="dot i-${d.intensity}"></span><span class="session">${d.session}</span>
        <span class="session-dur">${d.duration_min ? `${d.duration_min} min` : ""}</span>
        <div class="details">${d.details || ""}</div></td>
      <td class="status">${status}</td></tr>`;
  }).join("");
}

const STATUS_LABELS = {
  PRODUCTIVE: ["Productive", "var(--fitness-bg)", "var(--fitness)"],
  MAINTAINING: ["Maintaining", "var(--fitness-bg)", "var(--fitness)"],
  RECOVERY: ["Recovery", "var(--line)", "var(--muted)"],
  RECOVERY_ACTIVE: ["Active recovery", "var(--line)", "var(--muted)"],
  UNPRODUCTIVE_1: ["Unproductive", "var(--danger-bg)", "var(--danger)"],
  UNPRODUCTIVE_2: ["Unproductive", "var(--danger-bg)", "var(--danger)"],
  UNPRODUCTIVE_3: ["Unproductive", "var(--danger-bg)", "var(--danger)"],
  OVERREACHING: ["Overreaching", "var(--danger-bg)", "var(--danger)"],
  DETRAINING: ["Detraining", "#fff4e5", "var(--fatigue)"],
  PEAKING: ["Peaking", "var(--fitness-bg)", "var(--fitness)"],
};

function renderVO2(ts) {
  const el = $("vo2-panel");
  if (!el || !ts) return;
  const v = ts.vo2max_cycling || ts.vo2max_generic;
  if (!v) { el.innerHTML = '<span class="muted">No data</span>'; return; }
  el.innerHTML = `
    <div class="vo2-row">
      <div>
        <div class="vo2-num">${v.toFixed(1)}</div>
        <div class="vo2-sub">${ts.fitness_age ? "Fitness age " + ts.fitness_age : ""}</div>
      </div>
      <div class="vo2-trend">
        <div class="vo2-trend-label">Trend (from activities)</div>
        <svg id="vo2svg" viewBox="0 0 120 38"></svg>
        <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--muted);margin-top:2px">
          <span>12 wk ago</span><span>now</span>
        </div>
      </div>
    </div>`;
}

function renderVO2Trend(activities) {
  const svg = document.getElementById("vo2svg");
  if (!svg) return;
  const byWeek = {};
  activities.forEach(a => {
    if (!a.start || !a.avg_hr || !a.max_hr) return;
    const d = new Date(a.start);
    const wk = Math.floor((Date.now() - d) / (7 * 864e5));
    if (wk > 12) return;
    if (!byWeek[wk]) byWeek[wk] = [];
    const hrr = (a.avg_hr - 50) / (a.max_hr - 50);
    const effort = Math.max(0.3, Math.min(1, hrr));
    const load = a.training_load || 0;
    byWeek[wk].push(load / effort);
  });
  const weeks = Array.from({length: 13}, (_, i) => 12 - i);
  const vals = weeks.map(w => byWeek[w] ? byWeek[w].reduce((a,b)=>a+b,0)/byWeek[w].length : null);
  const filled = vals.map((v, i) => v ?? (i > 0 ? vals.slice(0,i).reverse().find(x=>x) : null) ?? 0);
  const mn = Math.min(...filled), mx = Math.max(...filled) || 1;
  const pts = filled.map((v, i) => `${(i/12)*120},${38 - ((v-mn)/(mx-mn||1))*30 - 4}`).join(" ");
  const last = filled[filled.length - 1];
  const lastX = 120, lastY = 38 - ((last-mn)/(mx-mn||1))*30 - 4;
  svg.innerHTML = `
    <polyline points="${pts}" fill="none" stroke="var(--line2)" stroke-width="1.5" stroke-linejoin="round"/>
    <polyline points="${pts}" fill="none" stroke="var(--fitness)" stroke-width="2" stroke-linejoin="round" opacity="0.8"/>
    <circle cx="${lastX}" cy="${lastY}" r="3" fill="var(--fitness)"/>`;
}

function renderGarminStatus(ts) {
  const el = $("garmin-status");
  if (!el || !ts) return;
  const key = ts.training_status || "";
  const [label, bg, fg] = STATUS_LABELS[key] || ["Unknown", "var(--line)", "var(--muted)"];
  const trendLabel = ts.fitness_trend === 1 ? "declining" : ts.fitness_trend === 2 ? "stable" : "improving";
  const trendColor = ts.fitness_trend === 1 ? "var(--danger)" : ts.fitness_trend === 2 ? "var(--fatigue)" : "var(--fitness)";
  el.innerHTML = `
    <div class="status-pill" style="background:${bg};color:${fg}">${label}</div>
    <table class="garmin-kv">
      <tr><td>Fitness trend</td><td style="color:${trendColor}">${trendLabel}</td></tr>
      <tr><td>ACWR ratio</td><td>${ts.garmin_acwr_ratio ? ts.garmin_acwr_ratio.toFixed(2) : "–"} <span style="color:var(--fitness);font-size:10px">${ts.acwr_status ? ts.acwr_status.toLowerCase() : ""}</span></td></tr>
      <tr><td>Acute load</td><td>${ts.garmin_acute_load ?? "–"}</td></tr>
      <tr><td>Chronic load</td><td>${ts.garmin_chronic_load ?? "–"}</td></tr>
    </table>`;
}

function renderLoadBalance(ts) {
  const el = $("load-balance");
  if (!el || !ts) return;
  const bands = [
    { name: "Aerobic low", sub: "zone 1-2", actual: ts.load_aerobic_low, target: ts.load_aerobic_low_target },
    { name: "Aerobic high", sub: "tempo / threshold", actual: ts.load_aerobic_high, target: ts.load_aerobic_high_target },
    { name: "Anaerobic", sub: "hard efforts", actual: ts.load_anaerobic, target: ts.load_anaerobic_target },
  ];
  const max = Math.max(...bands.map(b => Math.max(b.actual || 0, (b.target || [0,0])[1] * 1.6)));
  el.innerHTML = bands.map(b => {
    if (!b.actual || !b.target) return "";
    const [tMin, tMax] = b.target;
    const pct = v => Math.min(100, (v / max) * 100).toFixed(1);
    const over = b.actual > tMax;
    const under = b.actual < tMin;
    const cls = over ? "bal-over" : under ? "bal-warn" : "bal-ok";
    const dotCls = over ? "var(--danger)" : under ? "var(--fatigue)" : "var(--fitness)";
    const hint = over ? `${(b.actual/tMax).toFixed(1)}x target max` : under ? "below minimum" : "within range";
    return `<div class="balance-row">
      <div class="balance-head">
        <span class="balance-name">
          <span class="dot-status" style="background:${dotCls}"></span>
          ${b.name} <span style="color:var(--muted);font-weight:400">(${b.sub})</span>
        </span>
        <span class="balance-nums">${b.actual} · target ${tMin}–${tMax}</span>
      </div>
      <div class="bar-track">
        <div class="bar-target" style="left:${pct(tMin)}%;width:${pct(tMax-tMin)}%"></div>
        <div class="bar-fill ${cls}" style="width:${pct(b.actual)}%"></div>
      </div>
      <div class="balance-hint">${hint}</div>
    </div>`;
  }).join("");
}

async function main() {
  try {
    const [metrics, activities, daily, plan, meta, ts] = await Promise.all([
      load("metrics"), load("activities"), load("daily"),
      load("plan").catch(() => null), load("meta").catch(() => null),
      load("training_status").catch(() => null),
    ]);
    if (meta?.synced_at) {
      const mins = Math.round((Date.now() - new Date(meta.synced_at)) / 60000);
      $("synced").textContent = mins < 90 ? `synced ${mins} min ago`
        : `synced ${(mins / 60).toFixed(1)} h ago`;
    }
    renderGauge(metrics.current?.tsb ?? 0);
    renderStats(metrics, activities, daily);
    renderChart(metrics);
    renderMix(activities);
    renderRecovery(daily);
    renderVO2(ts);
    renderVO2Trend(activities);
    renderGarminStatus(ts);
    renderLoadBalance(ts);
    renderPlan(plan, activities);
  } catch (e) {
    document.body.insertAdjacentHTML("beforeend",
      `<p class="muted">No data yet. Run the sync workflow or scripts/make_sample_data.py. (${e.message})</p>`);
  }
}
main();
