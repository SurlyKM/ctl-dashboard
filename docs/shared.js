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

function _factorColor(val) {
  if (!val) return "var(--muted)";
  if (val === "very good" || val === "good") return "var(--fitness)";
  if (val === "moderate") return "var(--fatigue)";
  return "var(--danger)";
}

function renderGarminStatus(ts) {
  const el = $("garmin-status");
  if (!el || !ts) return;

  const r = ts.readiness_score != null;
  const readinessHtml = r ? (function() {
    const score = ts.readiness_score;
    const level = ts.readiness_level || "";
    const feedback = (ts.readiness_feedback || "").replace(/_/g, " ");
    const hours = ts.readiness_recovery_hours;
    const levelColors = {
      low:      ["var(--danger-bg)", "var(--danger)"],
      moderate: ["#fff4e5",          "var(--fatigue)"],
      high:     ["var(--fitness-bg)","var(--fitness)"],
    };
    const [pillBg, pillFg] = levelColors[level] || ["var(--line)", "var(--muted)"];
    const factors = [
      ["HRV",             ts.readiness_hrv_factor],
      ["Recovery time",   ts.readiness_recovery_factor],
      ["Sleep last night",ts.readiness_sleep_factor],
      ["Sleep history",   ts.readiness_sleep_history],
      ["Stress history",  ts.readiness_stress_history],
      ["ACWR",            ts.readiness_acwr_factor],
    ];
    return "<div class=\"readiness-wrap\">" +
      "<div class=\"readiness-score-row\">" +
        "<div><div class=\"label\">Readiness</div><div class=\"readiness-num\">" + score + "</div></div>" +
        "<div class=\"readiness-bar-wrap\">" +
          "<div class=\"readiness-bar-track\">" +
            "<div class=\"readiness-bar-fill\" style=\"width:" + score + "%;background:" + pillFg + ";opacity:0.7\"></div>" +
          "</div>" +
          "<div class=\"readiness-bar-labels\"><span>0</span><span>100</span></div>" +
        "</div>" +
      "</div>" +
      "<div class=\"status-pill\" style=\"background:" + pillBg + ";color:" + pillFg + "\">" + level + "</div>" +
      (feedback ? "<div class=\"readiness-feedback\">" + feedback + (hours ? " · " + hours + " hrs recovery remaining" : "") + "</div>" : "") +
      "<table class=\"garmin-kv\" style=\"margin-top:10px\">" +
      factors.map(function(f) {
        return "<tr><td>" + f[0] + "</td><td style=\"color:" + _factorColor(f[1]) + "\">" + (f[1] || "–") + "</td></tr>";
      }).join("") +
      "</table></div>";
  })() : "";

  const key = ts.training_status || "";
  const statusDetail = key.includes("—") ? key.split("—").slice(1).join("—").trim() : "";
  const trendLabel = ts.fitness_trend === 1 ? "declining" : ts.fitness_trend === 2 ? "stable" : "improving";
  const trendColor = ts.fitness_trend === 1 ? "var(--danger)" : ts.fitness_trend === 2 ? "var(--fatigue)" : "var(--fitness)";

  el.innerHTML = readinessHtml +
    (statusDetail ? "<div class=\"readiness-feedback\" style=\"margin-top:" + (r ? "14px" : "0") + ";border-top:" + (r ? "1px solid var(--line)" : "none") + ";padding-top:" + (r ? "10px" : "0") + "\">" + statusDetail + "</div>" : "") +
    "<table class=\"garmin-kv\" style=\"margin-top:8px\">" +
    "<tr><td>Fitness trend</td><td style=\"color:" + trendColor + "\">" + trendLabel + "</td></tr>" +
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
