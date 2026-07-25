// Shared utilities used by both dashboard.js and plan.js
const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const SPORT_LABELS = {
  cycling: "Cycling", mtb: "MTB", swim: "Swim", gym: "Gym",
  walk_hike: "Walk/hike", yoga: "Yoga", rest: "Rest", other: "Other",
};

const INTENSITY_PILL = {
  easy:     ["Easy",     "var(--fitness-bg)", "var(--fitness)"],
  moderate: ["Moderate", "#fff4e5",           "var(--fatigue)"],
  hard:     ["Hard",     "var(--danger-bg)",  "var(--danger)"],
};

const STATUS_LABELS = {
  PRODUCTIVE:      ["Productive",      "var(--fitness-bg)", "var(--fitness)"],
  MAINTAINING:     ["Maintaining",     "var(--fitness-bg)", "var(--fitness)"],
  RECOVERY:        ["Recovery",        "var(--line)",       "var(--muted)"],
  RECOVERY_ACTIVE: ["Active recovery", "var(--line)",       "var(--muted)"],
  UNPRODUCTIVE_1:  ["Unproductive",    "var(--danger-bg)",  "var(--danger)"],
  UNPRODUCTIVE_2:  ["Unproductive",    "var(--danger-bg)",  "var(--danger)"],
  UNPRODUCTIVE_3:  ["Unproductive",    "var(--danger-bg)",  "var(--danger)"],
  OVERREACHING:    ["Overreaching",    "var(--danger-bg)",  "var(--danger)"],
  DETRAINING:      ["Detraining",      "#fff4e5",           "var(--fatigue)"],
  PEAKING:         ["Peaking",         "var(--fitness-bg)", "var(--fitness)"],
};

async function load(name) {
  const r = await fetch("data/" + name + ".json", { cache: "no-store" });
  if (!r.ok) throw new Error(name + ".json " + r.status);
  return r.json();
}

function renderSynced(meta) {
  if (!meta?.synced_at) return;
  const mins = Math.round((Date.now() - new Date(meta.synced_at)) / 60000);
  const el = $("synced");
  if (el) el.textContent = mins < 90
    ? "synced " + mins + " min ago"
    : "synced " + (mins / 60).toFixed(1) + " h ago";
}

function tsbZone(tsb) {
  if (tsb < -25) return ["Overreached", "var(--danger-bg)",  "var(--danger)"];
  if (tsb < -10) return ["Fatigued",    "#fff4e5",           "var(--fatigue)"];
  if (tsb <   5) return ["Neutral",     "var(--line)",       "var(--ink2)"];
  if (tsb <  20) return ["Fresh",       "var(--fitness-bg)", "var(--fitness)"];
  return                ["Detraining",  "var(--line)",       "var(--muted)"];
}

function renderGarminStatus(ts) {
  const el = $("garmin-status");
  if (!el || !ts) return;
  const key = ts.training_status || "";
  const [label, bg, fg] = STATUS_LABELS[key] || ["Unknown", "var(--line)", "var(--muted)"];
  const trendLabel = ts.fitness_trend === 1 ? "declining" : ts.fitness_trend === 2 ? "stable" : "improving";
  const trendColor = ts.fitness_trend === 1 ? "var(--danger)" : ts.fitness_trend === 2 ? "var(--fatigue)" : "var(--fitness)";
  el.innerHTML =
    '<div class="status-pill" style="background:' + bg + ';color:' + fg + '">' + label + '</div>' +
    '<table class="garmin-kv">' +
    "<tr><td>Fitness trend</td><td style=\"color:" + trendColor + "\">" + trendLabel + "</td></tr>" +
    "<tr><td>ACWR ratio</td><td>" + (ts.garmin_acwr_ratio ? ts.garmin_acwr_ratio.toFixed(2) : "–") +
      ' <span style="color:var(--fitness);font-size:10px">' + (ts.acwr_status ? ts.acwr_status.toLowerCase() : "") + "</span></td></tr>" +
    "<tr><td>Acute load</td><td>" + (ts.garmin_acute_load ?? "–") + "</td></tr>" +
    "<tr><td>Chronic load</td><td>" + (ts.garmin_chronic_load ?? "–") + "</td></tr>" +
    "</table>";
}

function renderLoadBalance(ts) {
  const el = $("load-balance");
  if (!el || !ts) return;
  const bands = [
    { name: "Aerobic low",  sub: "zone 1-2",          actual: ts.load_aerobic_low,  target: ts.load_aerobic_low_target },
    { name: "Aerobic high", sub: "tempo / threshold",  actual: ts.load_aerobic_high, target: ts.load_aerobic_high_target },
    { name: "Anaerobic",    sub: "hard efforts",       actual: ts.load_anaerobic,    target: ts.load_anaerobic_target },
  ];
  const max = Math.max(...bands.map(b => Math.max(b.actual || 0, ((b.target || [0,0])[1]) * 1.6)));
  el.innerHTML = bands.map(b => {
    if (!b.actual || !b.target) return "";
    const [tMin, tMax] = b.target;
    const pct = v => Math.min(100, (v / max) * 100).toFixed(1);
    const over = b.actual > tMax, under = b.actual < tMin;
    const cls = over ? "bal-over" : under ? "bal-warn" : "bal-ok";
    const dot = over ? "var(--danger)" : under ? "var(--fatigue)" : "var(--fitness)";
    const hint = over ? (b.actual / tMax).toFixed(1) + "x target max" : under ? "below minimum" : "within range";
    return '<div class="balance-row">' +
      '<div class="balance-head">' +
        '<span class="balance-name"><span class="dot-status" style="background:' + dot + '"></span>' +
        b.name + ' <span style="color:var(--muted);font-weight:400">(' + b.sub + ')</span></span>' +
        '<span class="balance-nums">' + b.actual + " · target " + tMin + "–" + tMax + "</span>" +
      "</div>" +
      '<div class="bar-track">' +
        '<div class="bar-target" style="left:' + pct(tMin) + '%;width:' + pct(tMax - tMin) + '%"></div>' +
        '<div class="bar-fill ' + cls + '" style="width:' + pct(b.actual) + '%"></div>' +
      "</div>" +
      '<div class="balance-hint">' + hint + "</div>" +
      "</div>";
  }).join("");
}
