// Plan page — plan.html

// ---------------------------------------------------------------------------
// Details parsing. The coach writes either one line per item (midweek review
// format) or a single paragraph ("Warm-up: ... . Romanian Deadlift 3x8 (RPE 7),
// Side Plank 3x30s/side. Rest 90s ..."). Both are handled; anything that
// doesn't parse falls back to prose so nothing is ever dropped.
// ---------------------------------------------------------------------------

// Split on a separator regex, ignoring matches inside parentheses
function splitTop(text, sepRe) {
  const out = [];
  let depth = 0, buf = "";
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === "(") depth++;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    if (depth === 0) {
      const m = text.slice(i).match(sepRe);
      if (m && m.index === 0) {
        out.push(buf + (m[1] || ""));
        buf = "";
        i += m[0].length - 1;
        continue;
      }
    }
    buf += ch;
  }
  out.push(buf);
  return out.map(x => x.trim()).filter(Boolean);
}

// "Romanian Deadlift 3x8 (moderate load)" -> {name, sets, note}
const SETS_RE = /^(.+?)\s+(\d+\s*[x×]\s*\d+(?:\s*[-–]\s*\d+)?\s*(?:s|secs?|seconds|min|m|reps?)?(?:\s*(?:\/\s*|each\s+|per\s+)(?:side|leg|arm)s?)?)\s*\.?$/i;

function parseGymLine(line) {
  const note = (line.match(/\(([^)]+)\)/) || [])[1] || null;
  const stripped = line.replace(/\s*\([^)]*\)\s*/g, " ").replace(/\s+/g, " ").trim();
  const m = stripped.match(SETS_RE);
  if (!m) return null;
  return { name: m[1].replace(/^[-•*]\s*/, "").trim(), sets: m[2].replace(/\s+/g, ""), note };
}

const prose = t => '<p class="detail-prose">' + esc(t) + '</p>';
const noteDiv = t => '<div class="ex-note">' + esc(t) + '</div>';

function exerciseHtml(p) {
  const isFour = /^4\s*[x×]/i.test(p.sets);
  return '<div class="ex-item">' +
    '<div class="ex-left">' +
      '<span class="ex-name">' + esc(p.name) + '</span>' +
      (p.note ? '<span class="ex-inline-note">' + esc(p.note) + '</span>' : '') +
    '</div>' +
    '<div class="ex-sets' + (isFour ? " ex-sets-key" : "") + '">' + esc(p.sets.replace(/x/i, " × ")) + '</div>' +
    '</div>';
}

function parseGym(details) {
  // Units: lines if the coach used them, otherwise sentences
  const multiline = details.includes("\n");
  const units = multiline
    ? details.split("\n").map(l => l.trim()).filter(Boolean)
    : splitTop(details, /^\.(\s+)(?=[A-Z])/).map(u => u.replace(/\.$/, ""));

  let html = "", items = [], exCount = 0, title = null;
  const flush = () => {
    if (!items.length && !title) return;
    html += '<div class="ex-block">' +
      (title ? '<div class="ex-block-title">' + esc(title) + '</div>' : "") +
      items.join("") + '</div>';
    items = []; title = null;
  };

  units.forEach(u => {
    if (/^workout [ab]\b/i.test(u)) { flush(); title = u.replace(/:$/, ""); return; }
    if (/^warm.?up\b/i.test(u) || /^rest\b/i.test(u) || /^objective\b/i.test(u) || /^alternate\b/i.test(u)) {
      items.push(noteDiv(u)); return;
    }
    // A sentence may hold several comma-separated exercises
    const parts = multiline ? [u] : splitTop(u, /^,\s*/);
    const parsed = parts.map(parseGymLine);
    if (parsed.every(Boolean)) {
      parsed.forEach(p => { items.push(exerciseHtml(p)); exCount++; });
    } else {
      items.push(prose(u));
    }
  });
  flush();
  return exCount >= 2 ? html : null;
}

function parseSwim(details) {
  const PHASE_RE = /\b(warm-?up|main(?: set)?|cool-?down|total)\s*:\s*/gi;
  const marks = [];
  let m;
  while ((m = PHASE_RE.exec(details))) marks.push({ phase: m[1], start: m.index, body: PHASE_RE.lastIndex });
  if (marks.length < 2) return null;

  const lead = details.slice(0, marks[0].start).trim();
  let tail = "";
  const rows = marks.map((mk, i) => {
    let content = details.slice(mk.body, i + 1 < marks.length ? marks[i + 1].start : details.length).trim();
    if (i === marks.length - 1) {
      // Anything after the last phase's first sentence is commentary
      const cut = content.search(/\.\s+(?=[A-Z])/);
      if (cut !== -1) { tail = content.slice(cut + 1).trim(); content = content.slice(0, cut); }
    }
    content = content.replace(/[.;]\s*$/, "");
    const label = mk.phase.toLowerCase().replace(" set", "").replace("-", "");
    return '<div class="swim-row"><span class="swim-phase">' + esc(label) + '</span>' +
      '<span class="swim-content">' + esc(content) + '</span></div>';
  });
  return (lead ? prose(lead) : "") +
    '<div class="swim-block">' + rows.join("") + '</div>' +
    (tail ? prose(tail) : "");
}

function parseDetails(details, sport) {
  if (!details) return "";
  details = String(details).replace(/\r/g, "").trim();

  if (sport === "gym") {
    const g = parseGym(details);
    if (g) return g;
  }
  if (sport === "swim") {
    const sw = parseSwim(details);
    if (sw) return sw;
  }
  // Cycling / default: readable paragraphs
  return details.split(/\n{2,}/).map(par =>
    '<p class="detail-prose">' + esc(par).replace(/\n/g, "<br>") + '</p>').join("");
}

// Activities that count as a legitimate rest day
const REST_SUBS = new Set(["walk_hike", "yoga"]);

// Rides that satisfy each other. Garmin types MTB and road separately, but a
// planned ride is a planned ride, so either surface counts for either day.
const SPORT_EQUIV = {
  cycling: ["cycling", "mtb"],
  mtb:     ["mtb", "cycling"],
};

function sessionDone(planned, actualSet, isPast) {
  if (planned === "rest") {
    return isPast && (actualSet.size === 0 || [...actualSet].every(s => REST_SUBS.has(s)));
  }
  return (SPORT_EQUIV[planned] || [planned]).some(s => actualSet.has(s));
}

function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}

const dateKey = isoLocal;

function doneByDateOf(activities) {
  const out = {};
  (activities || []).forEach(a => {
    if (a.start) (out[a.start.slice(0, 10)] ||= new Set()).add(a.sport);
  });
  return out;
}

function renderPlan(plan, activities) {
  const container = $("plan-days");
  if (!plan?.days) {
    if (container) container.innerHTML = '<p class="muted">No plan generated yet.</p>';
    return;
  }

  const weekStart = new Date(plan.week_start + "T00:00");
  const weekEl = $("plan-week");
  if (weekEl) weekEl.textContent = "Week of " +
    weekStart.toLocaleDateString("en-AU", { day: "numeric", month: "long", year: "numeric" });

  const genEl = $("plan-generated");
  if (genEl) genEl.textContent = (plan.midweek_revision ? "revised " : "generated ") +
    new Date(plan.generated_at).toLocaleDateString("en-AU", { weekday: "short", day: "numeric", month: "short" });

  // Say clearly when the plan on screen isn't this week's
  const banner = $("plan-banner");
  if (banner) {
    const thisMon = mondayOf(new Date());
    const diffWeeks = Math.round((weekStart - thisMon) / (7 * 864e5));
    banner.innerHTML = diffWeeks === 0 ? ""
      : diffWeeks === 1 ? '<p class="plan-banner">This is next week\'s plan.</p>'
      : '<p class="plan-banner plan-banner-warn">This plan is for a past week. The weekly plan workflow may have failed.</p>';
  }

  const csEl = $("coach-says");
  if (csEl) {
    csEl.textContent = plan.coach_says || "";
    csEl.classList.remove("coach-clamped", "coach-expanded");
    const old = document.querySelector(".coach-toggle");
    if (old) old.remove();
    if (plan.coach_says && plan.coach_says.split(/\s+/).length > 40) {
      csEl.classList.add("coach-clamped");
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "coach-toggle";
      toggle.textContent = "Read more";
      toggle.setAttribute("aria-expanded", "false");
      toggle.addEventListener("click", () => {
        const expanded = csEl.classList.toggle("coach-expanded");
        toggle.textContent = expanded ? "Show less" : "Read more";
        toggle.setAttribute("aria-expanded", String(expanded));
      });
      csEl.after(toggle);
    }
  }

  const today0 = startOfToday();
  const doneByDate = doneByDateOf(activities);
  if (!container) return;

  container.innerHTML = plan.days.map((d, i) => {
    const date = new Date(weekStart);
    date.setDate(weekStart.getDate() + i);
    const isToday = date.getTime() === today0.getTime();
    const isPast = date < today0;
    const done = sessionDone(d.sport, doneByDate[dateKey(date)] || new Set(), isPast);

    const [intLabel, intBg, intFg] = INTENSITY_PILL[d.intensity] || INTENSITY_PILL.easy;
    const statusHtml = done
      ? '<span class="st-done">done</span>'
      : isToday ? '<span class="st-today">today</span>' : "";
    const bodyId = "day-body-" + i;

    return '<div class="day-card' + (isToday ? " expanded" : "") + (isPast && !isToday ? " past" : "") + '">' +
      '<button type="button" class="day-header" aria-expanded="' + isToday + '" aria-controls="' + bodyId + '">' +
        '<span class="day-lbl' + (isToday ? " day-today" : "") + '">' + esc(d.day) + '</span>' +
        '<span class="int-pill" style="background:' + intBg + ';color:' + intFg + '">' + intLabel + '</span>' +
        '<span class="day-session">' + esc(d.session) +
          (d.duration_min ? '<span class="day-dur"> · ' + esc(d.duration_min) + ' min</span>' : '') +
        '</span>' +
        '<span class="day-status">' + statusHtml + '</span>' +
        '<span class="day-chevron" aria-hidden="true">›</span>' +
      '</button>' +
      '<div class="day-body" id="' + bodyId + '">' + parseDetails(d.details, d.sport) + '</div>' +
    '</div>';
  }).join("");
}

function wirePlanDays() {
  const container = $("plan-days");
  if (!container) return;
  container.addEventListener("click", e => {
    const header = e.target.closest(".day-header");
    if (!header) return;
    const card = header.closest(".day-card");
    const isOpen = card.classList.contains("expanded");
    container.querySelectorAll(".day-card.expanded").forEach(c => {
      c.classList.remove("expanded");
      c.querySelector(".day-header").setAttribute("aria-expanded", "false");
    });
    if (!isOpen) {
      card.classList.add("expanded");
      header.setAttribute("aria-expanded", "true");
      if (card.getBoundingClientRect().top < 0) card.scrollIntoView({ block: "start" });
    }
  });
}

function renderCompliance(plan, activities) {
  const el = $("compliance");
  if (!el || !plan?.days) {
    if (el) el.innerHTML = '<span class="muted">No plan data</span>';
    return;
  }
  const weekStartC = new Date(plan.week_start + "T00:00");
  const doneByDate = doneByDateOf(activities);

  const today0 = startOfToday();
  let matched = 0;      // sessions banked across the whole week
  let elapsed = 0;      // days that have had a fair chance to happen
  let matchedElapsed = 0;

  const rows = plan.days.map((d, i) => {
    const date = new Date(weekStartC);
    date.setDate(weekStartC.getDate() + i);
    const dateStr = dateKey(date);
    const isToday = date.getTime() === today0.getTime();
    const isPast = date < today0;
    const future = date > today0;
    const actual_set = doneByDate[dateStr] || new Set();
    const done = sessionDone(d.sport, actual_set, isPast);

    if (done) matched++;
    // Today only counts against you once it is done, not while it is pending
    if (isPast || (isToday && done)) {
      elapsed++;
      if (done) matchedElapsed++;
    }

    const actual = actual_set.size
      ? Array.from(actual_set).map(s => SPORT_LABELS[s] || s).join(", ")
      : "—";
    const color = done ? "var(--fitness)" : future || isToday ? "var(--muted)" : "var(--fatigue)";
    const status = done ? "done" : future ? "upcoming" : isToday ? "today" : "missed";
    return "<tr><td class='day'>" + esc(d.day) + "</td>" +
      "<td class='comp-planned'>" + esc(SPORT_LABELS[d.sport] || d.sport) + "</td>" +
      "<td class='comp-actual'>" + esc(actual) + "</td>" +
      "<td class='status' style='color:" + color + "'>" + status + "</td></tr>";
  });

  // Colour the tally against days that have actually passed, not the full week
  const rate = elapsed ? matchedElapsed / elapsed : null;
  const tallyColor = rate === null ? "var(--muted)"
    : rate >= 0.7 ? "var(--fitness)" : "var(--fatigue)";

  el.innerHTML =
    '<div class="comp-head">' +
      '<span class="muted">Week of ' + esc(plan.week_start) + '</span>' +
      '<span class="comp-tally" style="color:' + tallyColor + '">' +
        matched + '/7 sessions' +
        (elapsed ? ' <span class="muted">(' + matchedElapsed + ' of ' + elapsed + ' so far)</span>' : '') +
      '</span>' +
    '</div>' +
    '<table class="kv">' + rows.join("") + '</table>';
}

let _wired = false;

async function main() {
  try {
    const [activities, plan, meta, ts] = await Promise.all([
      load("activities"),
      load("plan").catch(() => null),
      load("meta").catch(() => null),
      load("training_status").catch(() => null),
    ]);
    clearError();
    renderSynced(meta);
    renderPlan(plan, activities);
    renderCompliance(plan, activities);
    renderLoadBalance(ts);
    renderGarminStatus(ts);
  } catch (e) {
    showError("Couldn't load data (" + e.message + ").");
  }
  if (!_wired) {
    _wired = true;
    wirePlanDays();
    refreshOnReturn(main);
  }
}
main();
