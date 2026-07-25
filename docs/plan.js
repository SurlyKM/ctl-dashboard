// Plan page — plan.html

function parseDetails(details, sport) {
  if (!details) return "";

  // Gym: detect "Workout A/B:" followed by exercise lines
  if (sport === "gym" && /workout [ab]/i.test(details)) {
    const blocks = details.split(/\n\n+/);
    return blocks.map(block => {
      const lines = block.trim().split("\n").map(l => l.trim()).filter(Boolean);
      if (!lines.length) return "";
      const header = lines[0];
      const isWorkout = /^workout [ab]/i.test(header);
      if (isWorkout) {
        const noteMatch = details.match(/perform \d+ sets[^.]*\./i) ||
                         details.match(/rep ranges?[^.]*\./i) ||
                         details.match(/rest \d+[^.]*\./i);
        const exercises = lines.slice(1).filter(l =>
          !/^perform/i.test(l) && !/^rep range/i.test(l) &&
          !/^rest /i.test(l) && !/^alternate/i.test(l) && l.length > 2
        );
        const note = noteMatch ? noteMatch[0] : "3 sets · 15-18 / 12-15 / 8-12 reps";
        return '<div class="ex-block">' +
          '<div class="ex-block-title">' + header + '</div>' +
          '<div class="ex-note">' + note + '</div>' +
          exercises.map(ex => {
            const m = ex.match(/^(.+?)\s+(\d+x[\w\s\-\/()]+|3x[\w\s\-\/()]+|\d+\s*sets?.*)$/i);
            const name = m ? m[1].trim() : ex.replace(/\s+\d+x.*/i, "").trim();
            const sets = m ? m[2].trim() : "3 sets";
            return '<div class="ex-item"><span class="ex-name">' + name +
              '</span><div class="ex-meta"><div class="ex-sets">' + sets +
              '</div><div class="ex-reps">15-18 / 12-15 / 8-12</div></div></div>';
          }).join("") +
          '</div>';
      }
      return '<p class="detail-prose">' + block + '</p>';
    }).join("");
  }

  // Swim: detect phase keywords
  if (sport === "swim" || /warmup:|main:|cooldown:/i.test(details)) {
    const lines = details.split(/\n/).map(l => l.trim()).filter(Boolean);
    const phases = [], prose = [];
    lines.forEach(l => {
      const m = l.match(/^(warmup|main|cooldown|total):\s*(.+)$/i);
      if (m) phases.push({ phase: m[1], content: m[2] });
      else if (phases.length === 0) prose.push(l);
    });
    if (phases.length) {
      return (prose.length ? '<p class="detail-prose">' + prose.join(" ") + '</p>' : "") +
        '<div class="swim-block">' +
        phases.map(p =>
          '<div class="swim-row"><span class="swim-phase">' + p.phase + '</span>' +
          '<span class="swim-content">' + p.content + '</span></div>'
        ).join("") + '</div>';
    }
  }

  // Cycling / default: render as readable paragraphs
  return '<p class="detail-prose">' + details.replace(/\n\n/g, '</p><p class="detail-prose">').replace(/\n/g, "<br>") + '</p>';
}

function todayIndex() {
  const d = new Date().getDay();
  return d === 0 ? 6 : d - 1;
}

function renderPlan(plan, activities) {
  if (!plan?.days) return;

  const weekEl = $("plan-week");
  if (weekEl) weekEl.textContent = "Week of " +
    new Date(plan.week_start + "T00:00").toLocaleDateString("en-AU",
      {day:"numeric", month:"long", year:"numeric"});

  const genEl = $("plan-generated");
  if (genEl) genEl.textContent = "generated " +
    new Date(plan.generated_at).toLocaleDateString("en-AU");

  const csEl = $("coach-says");
  if (csEl) csEl.textContent = plan.coach_says || "";

  const weekStart = new Date(plan.week_start + "T00:00");
  const todayStr = new Date().toDateString();
  const todayIdx = todayIndex();

  const doneByDate = {};
  activities.forEach(a => {
    if (a.start) (doneByDate[a.start.slice(0,10)] ||= new Set()).add(a.sport);
  });

  const container = $("plan-days");
  if (!container) return;

  container.innerHTML = plan.days.map((d, i) => {
    const date = new Date(weekStart);
    date.setDate(weekStart.getDate() + i);
    const dateStr = date.toISOString().slice(0,10);
    const isToday = date.toDateString() === todayStr;
    const isPast = date < new Date() && !isToday;
    const done = doneByDate[dateStr]?.has(d.sport) ||
      (d.sport === "rest" && isPast && !doneByDate[dateStr]);

    const [intLabel, intBg, intFg] = INTENSITY_PILL[d.intensity] || INTENSITY_PILL.easy;
    const statusHtml = done
      ? '<span class="st-done">done</span>'
      : isToday ? '<span class="st-today">today</span>' : "";

    const detailHtml = parseDetails(d.details, d.sport);
    const expandedClass = isToday ? " expanded" : "";

    return '<div class="day-card' + expandedClass + '" data-idx="' + i + '">' +
      '<div class="day-header" onclick="toggleDay(this)">' +
        '<span class="day-lbl' + (isToday ? " day-today" : "") + '">' + d.day + '</span>' +
        '<span class="int-pill" style="background:' + intBg + ';color:' + intFg + '">' + intLabel + '</span>' +
        '<span class="day-session">' + d.session +
          (d.duration_min ? '<span class="day-dur"> · ' + d.duration_min + ' min</span>' : '') +
        '</span>' +
        '<span class="day-status">' + statusHtml + '</span>' +
        '<span class="day-chevron">›</span>' +
      '</div>' +
      '<div class="day-body">' + detailHtml + '</div>' +
    '</div>';
  }).join("");
}

function toggleDay(header) {
  const card = header.closest(".day-card");
  const isOpen = card.classList.contains("expanded");
  // Close all
  document.querySelectorAll(".day-card.expanded").forEach(c => c.classList.remove("expanded"));
  // Open this one unless it was already open
  if (!isOpen) card.classList.add("expanded");
}

function renderCompliance(plan, activities) {
  const el = $("compliance");
  if (!el || !plan?.days) {
    if (el) el.innerHTML = '<span class="muted">No previous plan data</span>';
    return;
  }
  const weekStart = new Date(plan.week_start + "T00:00");
  const doneByDate = {};
  activities.forEach(a => {
    if (a.start) (doneByDate[a.start.slice(0,10)] ||= new Set()).add(a.sport);
  });
  let matched = 0;
  const rows = plan.days.map((d, i) => {
    const date = new Date(weekStart);
    date.setDate(weekStart.getDate() + i);
    const dateStr = date.toISOString().slice(0,10);
    const done = doneByDate[dateStr]?.has(d.sport) ||
      (d.sport === "rest" && date < new Date() && !doneByDate[dateStr]);
    if (done) matched++;
    const actual = doneByDate[dateStr]
      ? Array.from(doneByDate[dateStr]).map(s => SPORT_LABELS[s]||s).join(", ")
      : "—";
    const future = date > new Date();
    const color = done ? "var(--fitness)" : future ? "var(--muted)" : "var(--fatigue)";
    const status = done ? "done" : future ? "upcoming" : "missed";
    return "<tr><td class='day'>" + d.day + "</td>" +
      "<td style='font-size:12px;color:var(--ink2)'>" + (SPORT_LABELS[d.sport]||d.sport) + "</td>" +
      "<td style='font-size:11px;color:var(--muted)'>" + actual + "</td>" +
      "<td class='status' style='color:" + color + "'>" + status + "</td></tr>";
  });
  const pct = Math.round((matched / plan.days.length) * 100);
  el.innerHTML =
    '<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px">' +
      '<span style="font-size:11px;color:var(--muted)">Week of ' + plan.week_start + '</span>' +
      '<span style="font-size:11px;font-weight:600;color:' +
        (pct >= 70 ? "var(--fitness)" : "var(--fatigue)") + '">' + matched + '/7 sessions</span>' +
    '</div>' +
    '<table class="kv">' + rows.join("") + '</table>';
}

async function main() {
  try {
    const [activities, plan, meta, ts] = await Promise.all([
      load("activities"),
      load("plan").catch(() => null),
      load("meta").catch(() => null),
      load("training_status").catch(() => null),
    ]);
    renderSynced(meta);
    renderPlan(plan, activities);
    renderCompliance(plan, activities);
    renderLoadBalance(ts);
    renderGarminStatus(ts);
  } catch(e) {
    document.body.insertAdjacentHTML("beforeend",
      '<p class="muted" style="padding:20px">No data yet. (' + e.message + ')</p>');
  }
}
main();
