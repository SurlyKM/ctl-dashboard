// Shared utilities used by both dashboard.js and plan.js
const $ = (id) => document.getElementById(id);

// Everything rendered via innerHTML that originates in data files (LLM plan
// text, Garmin strings) goes through esc() so a stray "<" or "&" can't break
// the layout or inject markup.
function esc(v) {
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const SPORT_LABELS = {
  cycling: "Cycling", mtb: "MTB", swim: "Swim", gym: "Gym",
  walk_hike: "Walk/hike", yoga: "Yoga", rest: "Rest", other: "Other",
};

const INTENSITY_PILL = {
  easy:     ["Easy",     "var(--fitness-bg)", "var(--fitness)"],
  moderate: ["Moderate", "var(--warn-bg)",    "var(--fatigue)"],
  hard:     ["Hard",     "var(--danger-bg)",  "var(--danger)"],
};

// Garmin sends codes like MAINTAINING_2; the suffix is stripped before lookup
const STATUS_LABELS = {
  PRODUCTIVE:      ["Productive",      "var(--fitness-bg)", "var(--fitness)"],
  MAINTAINING:     ["Maintaining",     "var(--fitness-bg)", "var(--fitness)"],
  RECOVERY:        ["Recovery",        "var(--line)",       "var(--muted)"],
  RECOVERY_ACTIVE: ["Active recovery", "var(--line)",       "var(--muted)"],
  UNPRODUCTIVE:    ["Unproductive",    "var(--danger-bg)",  "var(--danger)"],
  OVERREACHING:    ["Overreaching",    "var(--danger-bg)",  "var(--danger)"],
  DETRAINING:      ["Detraining",      "var(--warn-bg)",    "var(--fatigue)"],
  PEAKING:         ["Peaking",         "var(--fitness-bg)", "var(--fitness)"],
  STRAINED:        ["Strained",        "var(--danger-bg)",  "var(--danger)"],
  NO_STATUS:       ["No status",       "var(--line)",       "var(--muted)"],
};

function statusLabel(code) {
  if (!code) return null;
  const key = String(code).toUpperCase();
  const hit = STATUS_LABELS[key] || STATUS_LABELS[key.replace(/_\d+$/, "")];
  if (hit) return hit;
  const pretty = key.replace(/_\d+$/, "").replace(/_/g, " ").toLowerCase();
  return [pretty.charAt(0).toUpperCase() + pretty.slice(1), "var(--line)", "var(--muted)"];
}

// "no-cache" revalidates with the ETag GitHub Pages sends, so an unchanged
// 120 KB activities file costs a 304 instead of a full download on mobile.
async function load(name) {
  const r = await fetch("data/" + name + ".json", { cache: "no-cache" });
  if (!r.ok) throw new Error(name + ".json " + r.status);
  return r.json();
}

// Sync runs several times a day. Past this, something is probably broken
// (expired Garmin token, failed workflow) and the page should say so.
const STALE_HOURS = 12;

function renderSynced(meta) {
  const el = $("synced");
  if (!el || !meta?.synced_at) return;
  const t = new Date(meta.synced_at);
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  let txt;
  if (mins < 1) txt = "synced just now";
  else if (mins < 90) txt = "synced " + mins + " min ago";
  else if (mins < 48 * 60) txt = "synced " + Math.round(mins / 60) + " h ago";
  else txt = "synced " + Math.round(mins / 1440) + " days ago";
  el.textContent = txt;
  el.title = t.toLocaleString();
  el.classList.toggle("stale", mins > STALE_HOURS * 60);
}

// Mobile browsers (Chrome on iOS especially) keep tabs alive for days and
// restore them from memory without reloading. Re-run the page's render when
// it comes back to the foreground after a while so numbers aren't stale.
function refreshOnReturn(render, minAgeMs) {
  let last = Date.now();
  const maybe = () => {
    if (document.visibilityState !== "visible") return;
    if (Date.now() - last < (minAgeMs || 5 * 60000)) return;
    last = Date.now();
    render();
  };
  document.addEventListener("visibilitychange", maybe);
  window.addEventListener("pageshow", e => { if (e.persisted) maybe(); });
}

function showError(msg) {
  const host = document.querySelector("main") || document.body;
  let el = $("load-error");
  if (!el) {
    el = document.createElement("p");
    el.id = "load-error";
    el.className = "load-error";
    host.prepend(el);
  }
  el.textContent = msg;
}

function clearError() {
  const el = $("load-error");
  if (el) el.remove();
}

function tsbZone(tsb) {
  if (tsb < -25) return ["Overreached", "var(--danger-bg)",  "var(--danger)"];
  if (tsb < -10) return ["Fatigued",    "var(--warn-bg)",    "var(--fatigue)"];
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
  if (!el) return;
  if (!ts || !Object.keys(ts).length) {
    el.innerHTML = '<span class="muted">No Garmin assessment synced yet</span>';
    return;
  }

  const r = ts.readiness_score != null;
  let readinessHtml = "";
  if (r) {
    const score = Math.max(0, Math.min(100, Number(ts.readiness_score) || 0));
    const level = ts.readiness_level || "";
    const feedback = (ts.readiness_feedback || "").replace(/_/g, " ");
    const hours = ts.readiness_recovery_hours;
    const levelColors = {
      poor:     ["var(--danger-bg)", "var(--danger)"],
      low:      ["var(--danger-bg)", "var(--danger)"],
      moderate: ["var(--warn-bg)",   "var(--fatigue)"],
      high:     ["var(--fitness-bg)","var(--fitness)"],
      prime:    ["var(--fitness-bg)","var(--fitness)"],
    };
    const [pillBg, pillFg] = levelColors[level] || ["var(--line)", "var(--muted)"];
    const factors = [
      ["HRV",              ts.readiness_hrv_factor],
      ["Recovery time",    ts.readiness_recovery_factor],
      ["Sleep last night", ts.readiness_sleep_factor],
      ["Sleep history",    ts.readiness_sleep_history],
      ["Stress history",   ts.readiness_stress_history],
      ["Training load (ACWR)", ts.readiness_acwr_factor],
    ];
    readinessHtml = '<div class="readiness-wrap">' +
      '<div class="readiness-score-row">' +
        '<div><div class="label">Readiness</div><div class="readiness-num">' + score + '</div></div>' +
        '<div class="readiness-bar-wrap">' +
          '<div class="readiness-bar-track" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + score + '" aria-label="Training readiness">' +
            '<div class="readiness-bar-fill" style="width:' + score + '%;background:' + pillFg + '"></div>' +
          '</div>' +
          '<div class="readiness-bar-labels"><span>0</span><span>100</span></div>' +
        '</div>' +
      '</div>' +
      (level ? '<div class="status-pill" style="background:' + pillBg + ';color:' + pillFg + '">' + esc(level) + '</div>' : "") +
      (feedback ? '<div class="readiness-feedback">' + esc(feedback) +
        (hours ? " · " + esc(hours) + " h recovery remaining" : "") + '</div>' : "") +
      '<table class="garmin-kv">' +
      factors.map(f =>
        '<tr><td>' + f[0] + '</td><td style="color:' + _factorColor(f[1]) + '">' + esc(f[1] || "–") + '</td></tr>'
      ).join("") +
      '</table></div>';
  }

  const status = statusLabel(ts.training_status);
  const trendMap = {
    1: ["declining", "var(--danger)"],
    2: ["stable",    "var(--fatigue)"],
    3: ["improving", "var(--fitness)"],
  };
  const [trendLabel, trendColor] = trendMap[ts.fitness_trend] || ["–", "var(--muted)"];

  el.innerHTML = readinessHtml +
    '<table class="garmin-kv' + (r ? " garmin-kv-sep" : "") + '">' +
    (status ? '<tr><td>Training status</td><td style="color:' + status[2] + '">' + esc(status[0]) + '</td></tr>' : "") +
    '<tr><td>Fitness trend</td><td style="color:' + trendColor + '">' + trendLabel + '</td></tr>' +
    '</table>';
}

function renderLoadBalance(ts) {
  const el = $("load-balance");
  if (!el) return;
  const validTarget = t => Array.isArray(t) && t[0] != null && t[1] != null;
  const bands = [
    { name: "Aerobic low",  sub: "zone 1-2",          actual: ts?.load_aerobic_low,  target: ts?.load_aerobic_low_target },
    { name: "Aerobic high", sub: "tempo / threshold", actual: ts?.load_aerobic_high, target: ts?.load_aerobic_high_target },
    { name: "Anaerobic",    sub: "hard efforts",      actual: ts?.load_anaerobic,    target: ts?.load_anaerobic_target },
  ].filter(b => b.actual != null && validTarget(b.target));

  if (!bands.length) {
    el.innerHTML = '<span class="muted">No load balance data yet</span>';
    return;
  }
  const max = Math.max(...bands.map(b => Math.max(b.actual, b.target[1] * 1.6))) || 1;
  const pct = v => Math.max(0, Math.min(100, (v / max) * 100)).toFixed(1);

  el.innerHTML = bands.map(b => {
    const [tMin, tMax] = b.target;
    const over = b.actual > tMax, under = b.actual < tMin;
    const cls = over ? "bal-over" : under ? "bal-warn" : "bal-ok";
    const dot = over ? "var(--danger)" : under ? "var(--fatigue)" : "var(--fitness)";
    const hint = over ? (b.actual - tMax) + " above maximum" +
        (b.actual >= tMax * 1.15 ? " (" + (b.actual / tMax).toFixed(1) + "×)" : "")
      : under ? (tMin - b.actual) + " below minimum" : "within range";
    return '<div class="balance-row">' +
      '<div class="balance-head">' +
        '<span class="balance-name"><span class="dot-status" style="background:' + dot + '"></span>' +
        b.name + ' <span class="balance-sub">(' + b.sub + ')</span></span>' +
        '<span class="balance-nums">' + esc(b.actual) + " · target " + esc(tMin) + "–" + esc(tMax) + "</span>" +
      "</div>" +
      '<div class="bar-track">' +
        '<div class="bar-target" style="left:' + pct(tMin) + '%;width:' + pct(tMax - tMin) + '%"></div>' +
        '<div class="bar-fill ' + cls + '" style="width:' + pct(b.actual) + '%"></div>' +
      "</div>" +
      '<div class="balance-hint">' + hint + "</div>" +
      "</div>";
  }).join("");
}

// Local-date helpers shared by both pages
function isoLocal(d) {
  return d.getFullYear() + "-" +
    String(d.getMonth() + 1).padStart(2, "0") + "-" +
    String(d.getDate()).padStart(2, "0");
}

function mondayOf(d) {
  const m = new Date(d);
  m.setHours(0, 0, 0, 0);
  m.setDate(m.getDate() - ((m.getDay() + 6) % 7));
  return m;
}

// Plan is "current" when its week_start is this week's Monday
function planIsCurrent(plan) {
  return !!plan?.week_start && plan.week_start === isoLocal(mondayOf(new Date()));
}
