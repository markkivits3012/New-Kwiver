// Menu (course and tees), Score (handicap and hole-by-hole score) and
// Settings (GPS and remote status) screens, plus the banner that offers to
// switch course when the GPS is at another installed course. Talks to the
// server's /api/courses, /api/course, /api/player and /api/round (store.py);
// reads the map's state through window.APP (end of app.js).
(function () {
  "use strict";
  const A = window.APP, C = window.COURSE;
  if (!A) return;
  const $ = id => document.getElementById(id);
  const N = A.holes.length;
  const HERE_M = 300;          // within this of a course's box counts as "at" it
  const DETECT_EVERY_MS = 20000;
  const SAVE_DELAY_MS = 400;   // score taps are saved after this pause

  // ---- state ---------------------------------------------------------------------
  let courses = [];            // /api/courses: [{id, name, holes, par, bbox}]
  let current = null;          // id of the course on screen
  let player = { hcp: null, tees: {} };
  let card = null;             // this course's scorecard.json, or null
  let teeSets = A.mapTeeSets(); // [{name, frac, yards[], color?, rating?, slope?, est?}]
  let tee = 0;                 // index into teeSets
  let round = null;            // {course, tee, hcp, scores: [n or null per hole]}
  let open = null;             // "menu" | "score" | "settings" | null
  let sel = 0;                 // hole picked on the score screen
  let lastDetect = 0;
  const dismissed = {};        // courses the golfer said "not now" to

  const HDR = { "Content-Type": "application/json", "X-Trolley-Client": "1" };
  function api(url, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: HDR, body: JSON.stringify(body) };
    // GETs answer with the data; POSTs with {ok, msg}, where msg is the saved data or a message
    return fetch(url, opt).then(r => r.json().catch(() => ({})).then(j =>
      (r.ok ? (body === undefined ? j : j.msg) : Promise.reject(j.msg || r.status))));
  }
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  // ---- course data: par, stroke index, handicap ---------------------------------------
  // par and stroke index can differ by tee (scorecard.json: per tee, then for
  // the whole course, then the map)
  const fromCard = (key, i) => {
    const t = teeSets[tee];
    return (t && t[key] && t[key][i]) || (card && Array.isArray(card[key]) && card[key][i]) || null;
  };
  const par = i => fromCard("par", i) || A.holes[i].par;
  const strokeIndex = i => fromCard("hcp", i) || A.holes[i].hcp || null;
  const parTotal = () => A.holes.reduce((s, h, i) => s + (par(i) || 0), 0);
  const fmtHcp = h => (h == null ? "None" : h < 0 ? `+${(-h).toFixed(1)}` : h.toFixed(1));
  const fmtPar = d => (d === 0 ? "E" : d > 0 ? `+${d}` : `${d}`);

  // Course handicap (WHS): index x slope / 113 + (rating - par). Without a
  // slope and rating, the index itself, scaled for courses under 18 holes.
  function courseHcp() {
    const h = round ? round.hcp : player.hcp;
    if (h == null) return null;
    const t = teeSets[tee];
    if (t && t.slope && t.rating) return Math.round(h * t.slope / 113 + (t.rating - parTotal()));
    return Math.round(h * N / 18);
  }
  // Strokes received on hole i: the course handicap spread by stroke index
  // (1 = hardest). Plus handicaps give strokes back on the easiest holes.
  function strokesOn(i, ch) {
    const si = strokeIndex(i);
    if (ch == null || !si) return 0;
    if (ch >= 0) return Math.floor(ch / N) + (si <= ch % N ? 1 : 0);
    return -(Math.floor(-ch / N) + (si > N - (-ch % N) ? 1 : 0));
  }
  function totals() {
    const s = round ? round.scores : [];
    const played = s.map((v, i) => (v == null ? null : i)).filter(i => i != null);
    const gross = played.reduce((t, i) => t + s[i], 0);
    const toPar = played.reduce((t, i) => t + s[i] - (par(i) || 0), 0);
    const ch = courseHcp();
    let net = null;
    if (ch != null && played.length) {
      if (A.holes.every((h, i) => strokeIndex(i))) net = gross - played.reduce((t, i) => t + strokesOn(i, ch), 0);
      else if (played.length === N) net = gross - ch; // no stroke index: only the full round
    }
    return { played: played.length, gross, toPar, net, ch };
  }

  // ---- tees ------------------------------------------------------------------------
  function teeSetsFor(c) {
    if (!c || !Array.isArray(c.tees) || !c.tees.length) return A.mapTeeSets();
    const holes = a => (Array.isArray(a) && a.length === N ? a : null);
    const sets = c.tees.map((t, k, all) => ({
      name: String(t.name), color: t.color, rating: t.rating, slope: t.slope,
      yards: holes(t.yards), par: holes(t.par), hcp: holes(t.hcp),
      frac: all.length === 1 ? 0 : k / (all.length - 1),
    }));
    // which mapped tee box to measure from: by length when every set has yards
    // (two sets of the same length share a box), else by order back to front
    const len = sets.map(teeTotal);
    if (len.every(Boolean) && Math.max(...len) > Math.min(...len)) {
      const hi = Math.max(...len), lo = Math.min(...len);
      sets.forEach((s, k) => { s.frac = (hi - len[k]) / (hi - lo); });
    }
    return sets;
  }
  function applyTee() { A.setTees(teeSets[tee]); }
  function chooseTee(k) {
    tee = k;
    applyTee();
    const name = teeSets[k].name;
    api("/api/player", { tees: { [current]: name } }).then(p => { player = p; }).catch(() => {});
    if (round) { round.tee = name; saveRound(); }
    draw();
  }
  const teeTotal = t => (t.yards ? t.yards.reduce((s, y) => s + y, 0) : null);

  // ---- round -----------------------------------------------------------------------
  let saveTimer = null;
  function saveRound() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => api("/api/round", round).catch(e => toast(`Score not saved: ${e}`)), SAVE_DELAY_MS);
  }
  function setScore(i, v) {
    if (!round) round = { course: current, tee: teeSets[tee].name, hcp: player.hcp, scores: Array(N).fill(null) };
    round.scores[i] = v == null ? null : Math.max(1, Math.min(30, v));
    saveRound();
    badge();
    draw();
  }
  function finishRound(save) {
    clearTimeout(saveTimer);
    const had = round;
    round = null;
    badge();
    if (!had) return Promise.resolve();
    // a pending save may not have gone out yet: send the last state, then finish
    return api("/api/round", had).catch(() => {})
      .then(() => api("/api/round/finish", { save }))
      .then(msg => toast(msg), e => toast(`Not saved: ${e}`));
  }

  // ---- course switching and detection -------------------------------------------------
  function distTo(c, lat, lon) {
    const [s, w, n, e] = c.bbox;
    const kx = 111320 * Math.cos(lat * Math.PI / 180), ky = 110540;
    return Math.hypot(Math.max(w - lon, 0, lon - e) * kx, Math.max(s - lat, 0, lat - n) * ky);
  }
  // the course whose holes are nearest (within HERE_M); courses side by side
  // (Fairchild Wheeler Red and Black) overlap, and a tie keeps the current one
  function courseAt(lat, lon) {
    let best = null, bd = HERE_M;
    for (const c of courses) {
      const d = distTo(c, lat, lon);
      if (d < bd || (d === bd && c.id === current)) { best = c; bd = d; }
    }
    return best;
  }
  A.hooks.fix = f => {
    if (!courses.length || Date.now() - lastDetect < DETECT_EVERY_MS) return;
    lastDetect = Date.now();
    const at = courseAt(f.lat, f.lon);
    banner(at && at.id !== current && !dismissed[at.id] ? at : null);
  };
  let bannerFor = null;
  function banner(c) {
    bannerFor = c;
    $("coursebanner").hidden = !c;
    if (c) $("coursebannertext").innerHTML = `GPS says you're at<b>${esc(c.name)}</b>`;
  }

  function askSwitch(c) {
    const scored = round && round.scores.some(s => s != null);
    modal(`<h2>Switch to ${esc(c.name)}?</h2>
      <p>The screen reloads with the new course.${scored ? ` Your round at ${esc(C.name)} is saved and ended.` : ""}</p>
      <div class="mrow"><button class="tbtn" data-act="close">Cancel</button>
      <button class="tbtn go" data-act="switch" data-arg="${esc(c.id)}">Switch</button></div>`);
  }
  function switchCourse(id) {
    const c = courses.find(x => x.id === id);
    modal(`<h2>Loading ${esc(c ? c.name : id)}…</h2><p>One moment.</p>`, true);
    const go = round && round.scores.some(s => s != null) ? finishRound(true) : finishRound(false);
    go.then(() => api("/api/course", { id }))
      .then(() => {
        // the server restarts on the new course; reload once it answers for it
        const t0 = Date.now();
        const poll = () => api("/api/courses")
          .then(r => (r.current === id || Date.now() - t0 > 30000 ? location.reload() : setTimeout(poll, 800)))
          .catch(() => setTimeout(poll, 800));
        setTimeout(poll, 1200);
      })
      .catch(e => modal(`<h2>Couldn't switch</h2><p>${esc(e)}</p>
        <div class="mrow"><button class="tbtn" data-act="close">OK</button></div>`));
  }

  // ---- sheets (full-screen panels) ---------------------------------------------------
  function show(which) {
    open = which;
    if (which === "score") sel = A.hole();
    $("sheet").hidden = !which;
    draw();
  }
  function draw() {
    if (!open) return;
    $("sheettitle").textContent = { menu: "Menu", score: "Score", settings: "Settings" }[open];
    const stop = A.trolley() ? `<button class="tbtn stop" data-act="stop">STOP</button>` : "";
    if (open === "menu") { $("sheettools").innerHTML = stop; $("sheetbody").innerHTML = menuHtml(); }
    if (open === "score") { $("sheettools").innerHTML = scoreTools() + stop; $("sheetbody").innerHTML = scoreHtml(); }
    if (open === "settings") { $("sheettools").innerHTML = stop; $("sheetbody").innerHTML = settingsHtml(); }
  }

  function menuHtml() {
    const f = A.fix();
    const fresh = f && Date.now() - f.t < A.STALE_MS;
    const list = courses.map(c => {
      let where = "";
      if (fresh) {
        const d = distTo(c, f.lat, f.lon);
        where = d < HERE_M ? "You're here" : `${(d / 1609.34).toFixed(d < 16093 ? 1 : 0)} mi away`;
      }
      return `<button class="item${c.id === current ? " on" : ""}" data-act="course" data-arg="${esc(c.id)}">
        <span class="itemname">${esc(c.name)}</span>
        <span class="itemsub">${c.holes} holes · par ${c.par}${where ? ` · ${where}` : ""}</span>
        ${c.id === current ? `<span class="tag">Playing</span>` : ""}</button>`;
    }).join("") || `<p class="note">Course list unavailable.</p>`;
    const tees = teeSets.map((t, k) => {
      const yds = teeTotal(t);
      const sub = [yds ? `${yds.toLocaleString("en-US")} yds` : null,
                   t.rating && t.slope ? `${t.rating} / ${t.slope}` : null].filter(Boolean).join(" · ");
      return `<button class="item${k === tee ? " on" : ""}" data-act="tee" data-arg="${k}">
        <span class="itemname">${t.color ? `<i class="teedot" style="background:${esc(t.color)}"></i>` : ""}${esc(t.name)}</span>
        <span class="itemsub">${sub}</span></button>`;
    }).join("");
    const est = teeSets[0] && teeSets[0].est;
    return `<div class="cols">
      <div class="col"><div class="eyebrow">COURSE</div><div class="list">${list}</div></div>
      <div class="col"><div class="eyebrow">TEES</div><div class="list">${tees}</div>
        ${est ? `<p class="note">Tees and yards are estimated from the map. Add this course's scorecard for exact yards, slope and rating.</p>` : ""}
      </div></div>`;
  }

  function scoreTools() {
    return `<button class="chipbtn" data-act="hcp">HCP <b>${fmtHcp(round ? round.hcp : player.hcp)}</b></button>
      <span class="chipinfo">${esc(teeSets[tee].name)} tees</span>`;
  }

  function scoreHtml() {
    const s = round ? round.scores : Array(N).fill(null);
    const ch = courseHcp();
    const p = par(sel), v = s[sel], si = strokeIndex(sel), got = strokesOn(sel, ch);
    const dots = got > 0 ? `<span class="dots">${"●".repeat(got)}</span>` : got < 0 ? `<span class="dots">+${-got}</span>` : "";
    const left = `<div class="scoreleft">
      <div class="holepick">
        <button class="nav small" data-act="sel" data-arg="${sel - 1}">‹</button>
        <div><div class="eyebrow">HOLE</div><div class="bigno">${A.holes[sel].no}</div></div>
        <button class="nav small" data-act="sel" data-arg="${sel + 1}">›</button>
      </div>
      <div class="holeinfo">Par ${p ?? "?"}${si ? ` · SI ${si}` : ""} ${dots}</div>
      <div class="stepper">
        <button class="step" data-act="minus">−</button>
        <div class="stepval ${cls(v, p)}">${v ?? "–"}</div>
        <button class="step" data-act="plus">+</button>
      </div>
      <div class="mrow"><button class="tbtn" data-act="par">Par</button><button class="tbtn" data-act="clear">Clear</button></div>
    </div>`;
    // the card: rows of up to 9 holes, each with its subtotal
    let grid = "";
    for (let a = 0; a < N; a += 9) {
      const idx = [...Array(Math.min(9, N - a)).keys()].map(k => a + k);
      const sub = idx.reduce((t, i) => t + (s[i] || 0), 0), subPar = idx.reduce((t, i) => t + (par(i) || 0), 0);
      const label = N <= 9 ? "Tot" : a === 0 ? "Out" : "In";
      grid += `<div class="cardrow">${idx.map(i => `<button class="cell${i === sel ? " sel" : ""}" data-act="sel" data-arg="${i}">
          <span class="ch">${A.holes[i].no}</span><span class="cp">${par(i) ?? ""}</span>
          <span class="cs ${cls(s[i], par(i))}">${s[i] ?? ""}</span></button>`).join("")}
        <div class="cell sum"><span class="ch">${label}</span><span class="cp">${subPar}</span><span class="cs">${sub || ""}</span></div></div>`;
    }
    const t = totals();
    const sum = `<div class="totals">
      <div><span>Gross</span><b>${t.played ? t.gross : "–"}</b></div>
      <div><span>To par</span><b>${t.played ? fmtPar(t.toPar) : "–"}</b></div>
      <div><span>Net${ch != null ? ` (CH ${ch})` : ""}</span><b>${t.net ?? "–"}</b></div>
      <div><span>Thru</span><b>${t.played}</b></div></div>`;
    return `<div class="cols">${left}<div class="col">${grid}${sum}
      <div class="mrow"><button class="tbtn" data-act="finish"${round ? "" : " disabled"}>Finish round</button></div></div></div>`;
  }
  function cls(v, p) {
    if (v == null || !p) return "";
    const d = v - p;
    return d <= -2 ? "eagle" : d === -1 ? "birdie" : d === 0 ? "par" : d === 1 ? "bogey" : "double";
  }

  function settingsHtml() {
    const rx = A.rx(), f = A.fix(), now = Date.now();
    const fresh = f && now - f.t < A.STALE_MS;
    let state, dot;
    if (!rx) [state, dot] = ["Waiting for the map server", "bad"];
    else if (rx.sim) [state, dot] = ["Simulated GPS", "ok"];
    else if (!rx.gpsd) [state, dot] = ["GPS service not running", "bad"];
    else if (!rx.device) [state, dot] = ["No GPS receiver plugged in", "bad"];
    else if (fresh && rx.mode >= 3) [state, dot] = ["3D fix", "ok"];
    else if (fresh) [state, dot] = ["2D fix (weak)", "weak"];
    else [state, dot] = ["Searching for satellites", "weak"];
    const acc = fresh && f.acc ? `±${Math.round(f.acc * A.YD)} yd` : "–";
    const age = f ? `${Math.round((now - f.t) / 1000)} s ago` : "Never";
    const pos = fresh ? `${f.lat.toFixed(5)}, ${f.lon.toFixed(5)}` : "–";
    const sats = rx && !rx.sim && rx.seen != null ? `${rx.used ?? 0} used of ${rx.seen} seen` : "–";
    const row = (k, v) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`;
    const T = A.trolley();
    const trolley = T ? `<div class="card"><div class="cardhead"><i class="sdot ${T.lost ? "bad" : "ok"}"></i>Trolley</div>
      ${row("Link", T.lost ? "No contact" : T.mock ? "Mock (testing)" : "Connected")}
      ${T.lost ? "" : row("Doing", esc(T.msg)) + row("Battery", `${T.battery}%`)}</div>` : "";
    return `<div class="cols">
      <div class="col"><div class="card"><div class="cardhead"><i class="sdot ${dot}"></i>GPS</div>
        ${row("Status", state)}${row("Satellites", sats)}${row("Accuracy", acc)}${row("Last fix", age)}${row("Position", pos)}
      </div></div>
      <div class="col"><div class="card"><div class="cardhead"><i class="sdot off"></i>Remote control</div>
        ${row("Status", "Not set up")}
        <p class="note">The handheld remote isn't built yet. Its connection will show here.</p></div>
        ${trolley}
        <div class="mrow"><button class="tbtn" data-act="touch">Touch calibration</button></div>
      </div></div>`;
  }

  // ---- handicap keypad ---------------------------------------------------------------
  let pad = "";
  let padPlus = false;
  function keypad() {
    const h = round ? round.hcp : player.hcp;
    padPlus = h != null && h < 0;
    pad = h == null ? "" : Math.abs(h).toFixed(1).replace(/\.0$/, "");
    drawPad();
  }
  function drawPad() {
    const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "0", "⌫"];
    modal(`<h2>Handicap index</h2>
      <div class="padval">${padPlus ? "+" : ""}${pad || "&nbsp;"}</div>
      <div class="pad">${keys.map(k => `<button class="tbtn" data-act="key" data-arg="${k}">${k}</button>`).join("")}</div>
      <div class="mrow"><button class="tbtn${padPlus ? " on" : ""}" data-act="plus-hcp">Plus</button>
        <button class="tbtn" data-act="no-hcp">None</button>
        <button class="tbtn" data-act="close">Cancel</button>
        <button class="tbtn go" data-act="save-hcp">Save</button></div>`, true);
  }
  function key(k) {
    if (k === "⌫") pad = pad.slice(0, -1);
    else if (k === "." ? !pad.includes(".") : !/\.\d$/.test(pad) && pad.replace(".", "").length < 3) pad += k;
    drawPad();
  }
  function saveHcp(v) {
    if (v != null && !(v >= -10 && v <= 54)) return toast("Handicap must be between +10 and 54.");
    api("/api/player", { hcp: v }).then(p => {
      player = p;
      if (round) { round.hcp = p.hcp; saveRound(); }
      closeModal();
      draw();
      badge();
    }, e => toast(String(e)));
  }

  // ---- modal and toast ---------------------------------------------------------------
  function modal(html, wide) {
    $("modalbox").className = wide ? "wide" : "";
    $("modalbox").innerHTML = html;
    $("modal").hidden = false;
  }
  function closeModal() { $("modal").hidden = true; }
  let toastTimer = null;
  function toast(text) {
    let t = $("toast");
    if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
  }

  // Score button: to-par so far while a round is going
  function badge() {
    const t = totals();
    $("btnscorelabel").textContent = round && t.played ? `Score ${fmtPar(t.toPar)}` : "Score";
  }

  // ---- input: buttons fire on release (resistive screen, see onTap in app.js) ---------
  function onRelease(root, fn) {
    let down = null;
    root.addEventListener("pointerdown", e => {
      down = e.target.closest("[data-act], .toolbtn, #sheetclose");
      if (!down) return;
      if (down.dataset.act === "stop") { A.stopTrolley(); down = null; return; } // on touch-down, like the bar
      root.setPointerCapture(e.pointerId);
    });
    root.addEventListener("pointerup", () => {
      const el = down;
      down = null;
      if (el && !el.disabled) fn(el.dataset.act || el.id, el.dataset.arg);
    });
    root.addEventListener("pointercancel", () => { down = null; });
  }
  function act(a, arg) {
    const s = round ? round.scores[sel] : null;
    switch (a) {
      case "btnmenu": return show(open === "menu" ? null : "menu");
      case "btnscore": return show(open === "score" ? null : "score");
      case "btnsettings": return show(open === "settings" ? null : "settings");
      case "sheetclose": return show(null);
      case "course": {
        const c = courses.find(x => x.id === arg);
        return c && c.id !== current ? askSwitch(c) : undefined;
      }
      case "switch": return switchCourse(arg);
      case "tee": return chooseTee(+arg);
      case "sel": sel = (+arg + N) % N; return draw();
      case "minus": return setScore(sel, s == null ? (par(sel) || 4) - 1 : s - 1);
      case "plus": return setScore(sel, s == null ? (par(sel) || 4) + 1 : s + 1);
      case "par": return setScore(sel, par(sel) || 4);
      case "clear": return setScore(sel, null);
      case "finish":
        return modal(`<h2>Finish this round?</h2><p>It's saved to the round history and the card is cleared.</p>
          <div class="mrow"><button class="tbtn" data-act="close">Cancel</button>
          <button class="tbtn" data-act="discard">Clear, don't save</button>
          <button class="tbtn go" data-act="finish-yes">Finish</button></div>`, true);
      case "finish-yes": closeModal(); return finishRound(true).then(draw);
      case "discard": closeModal(); return finishRound(false).then(draw);
      case "hcp": return keypad();
      case "key": return key(arg);
      case "plus-hcp": padPlus = !padPlus; return drawPad();
      case "no-hcp": return saveHcp(null);
      case "save-hcp": {
        if (pad === "" || pad === ".") return saveHcp(null);
        return saveHcp((padPlus ? -1 : 1) * parseFloat(pad));
      }
      case "close": return closeModal();
      case "touch": location.href = "touchtest.html"; return;
      case "coursebannergo": return bannerFor && askSwitch(bannerFor);
      case "coursebannerno": if (bannerFor) dismissed[bannerFor.id] = true; return banner(null);
    }
  }
  onRelease($("toolbar"), act);
  onRelease($("sheet"), act);
  onRelease($("modal"), act);
  onRelease($("coursebanner"), act);

  A.hooks.render = badge;
  setInterval(() => { if (open === "settings" || open === "menu") draw(); }, 1000);

  // ---- start ---------------------------------------------------------------------------
  Promise.all([
    api("/api/courses").catch(() => null),
    api("/api/player").catch(() => null),
    api("/api/round").catch(() => null),
    fetch("scorecard.json").then(r => (r.ok ? r.json() : null)).catch(() => null),
  ]).then(([cs, p, r, sc]) => {
    if (cs) { courses = cs.courses; current = cs.current; }
    if (p) player = { hcp: null, tees: {}, ...p };
    card = sc;
    teeSets = teeSetsFor(card);
    const saved = teeSets.findIndex(t => t.name === (player.tees || {})[current]);
    const preset = teeSets.findIndex(t => card && t.name === card.default_tee);
    tee = saved >= 0 ? saved : preset >= 0 ? preset : Math.floor((teeSets.length - 1) / 2); // middle unless set
    applyTee();
    if (r && r.scores) {
      // a round from another course (or an older layout) can't go on here: keep it in history
      if (r.course === current && r.scores.length === N) round = r;
      else api("/api/round/finish", { save: true }).catch(() => {});
    }
    badge();
    const f = A.fix();
    if (f) A.hooks.fix(f);
  });
})();
