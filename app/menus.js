// Menu (course and tees), Score (handicap, hole-by-hole score, past rounds) and
// Settings (GPS and remote status, My Bag) screens, the banner that offers to
// switch course when the GPS is at another installed course, and the club
// suggestion under the Center yardage. Talks to the server's /api/courses,
// /api/course, /api/player, /api/round, /api/rounds and /api/bag (store.py),
// and /api/remote (remote.py);
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
  const HIST_PAGE = 4;         // past rounds listed per page
  const CLUBS = ["Driver", "3 wood", "5 wood", "7 wood", "2 hybrid", "3 hybrid", "4 hybrid", "5 hybrid",
                 "3 iron", "4 iron", "5 iron", "6 iron", "7 iron", "8 iron", "9 iron", "PW", "GW", "SW", "LW"];
  const CLUB_MIN_YD = 20;      // nearer the pin than this: no club suggested (putting)
  const CLUB_SLACK_YD = 3;     // a club this much short of the distance still counts as reaching
  const MAX_CUSTOM = 10;       // own clubs ("52°", "Chipper") on top of CLUBS; the grid fits 30 tiles
  const NAME_LEN = 12;

  // ---- state ---------------------------------------------------------------------
  let courses = [];            // /api/courses: [{id, name, holes, par, bbox}]
  let current = null;          // id of the course on screen
  let player = { hcp: null, tees: {} };
  let card = null;             // this course's scorecard.json, or null
  let teeSets = A.mapTeeSets(); // [{name, frac, yards[], color?, rating?, slope?, est?}]
  let tee = 0;                 // index into teeSets
  let round = null;            // {course, tee, hcp, scores: [n or null per hole]}
  let open = null;             // "menu" | "score" | "history" | "settings" | "bag" | null
  let sel = 0;                 // hole picked on the score screen
  let history = null;          // /api/rounds: finished rounds, newest first
  let histSel = 0, histPage = 0;
  let bag = { clubs: {} };     // /api/bag: {club: carry yards}
  let remote = null;           // /api/remote: the handheld remote's link, while Settings is open
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
  // the course name and pars travel with the round, for the history
  const stamped = r => ({ ...r, name: C.name, par: A.holes.map((h, i) => par(i) || null) });
  function saveRound() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => api("/api/round", stamped(round)).catch(e => toast(`Score not saved: ${e}`)), SAVE_DELAY_MS);
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
    history = null; // fetched again when next shown
    return api("/api/round", stamped(had)).catch(() => {})
      .then(() => api("/api/round/finish", { save }))
      .then(msg => toast(msg), e => toast(`Not saved: ${e}`));
  }

  // ---- course switching and detection -------------------------------------------------
  function distTo(c, lat, lon) {
    const [s, w, n, e] = c.bbox;
    const kx = 111320 * Math.cos(lat * Math.PI / 180), ky = 110540;
    return Math.hypot(Math.max(w - lon, 0, lon - e) * kx, Math.max(s - lat, 0, lat - n) * ky);
  }
  const distYd = (a, b) => Math.hypot((a.lon - b.lon) * 111320 * Math.cos(a.lat * Math.PI / 180),
                                      (a.lat - b.lat) * 110540) * A.YD;
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
    if (which === "score" && open !== "history") sel = holeNow();
    if (which === "history" && !history) {
      histSel = histPage = 0;
      api("/api/rounds").then(r => { history = r; draw(); }, e => toast(`Couldn't load rounds: ${e}`));
    }
    open = which;
    $("sheet").hidden = !which;
    draw();
  }
  function draw() {
    if (!open) return;
    $("sheettitle").textContent = { menu: "Menu", score: "Score", history: "Past rounds", settings: "Settings", bag: "My Bag" }[open];
    const stop = A.trolley() ? `<button class="tbtn stop" data-act="stop">STOP</button>` : "";
    const back = (to, label) => `<button class="chipbtn" data-act="${to}">‹ <b>${label}</b></button>`;
    const [tools, body] = {
      menu: () => ["", menuHtml()],
      score: () => [scoreTools(), scoreHtml()],
      history: () => [back("btnscore", "Score"), historyHtml()],
      settings: () => ["", settingsHtml()],
      bag: () => [`<span class="chipinfo">Tap a club to set its carry</span>` + back("btnsettings", "Settings"), bagHtml()],
    }[open]();
    $("sheettools").innerHTML = tools + stop;
    $("sheetbody").innerHTML = body;
  }

  // The hole to score when the Score screen opens: the one the GPS puts the
  // cart on (else the one on screen). At a tee with that hole and the one
  // before both blank, it's the hole just finished that wants its score.
  function holeNow() {
    const g = A.gpsHole();
    const i = g == null ? A.hole() : g;
    const s = round ? round.scores : [];
    if (g != null && i > 0 && A.atTee(i) && s[i] == null && s[i - 1] == null) return i - 1;
    return i;
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
      <span class="chipinfo">${esc(teeSets[tee].name)} tees</span>
      <button class="chipbtn" data-act="history">Past rounds</button>`;
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
    const grid = cardGrid(s, A.holes.map((h, i) => par(i)), A.holes.map(h => h.no), sel);
    const t = totals();
    const sum = `<div class="totals">
      <div><span>Gross</span><b>${t.played ? t.gross : "–"}</b></div>
      <div><span>To par</span><b>${t.played ? fmtPar(t.toPar) : "–"}</b></div>
      <div><span>Net${ch != null ? ` (CH ${ch})` : ""}</span><b>${t.net ?? "–"}</b></div>
      <div><span>Thru</span><b>${t.played}</b></div></div>`;
    return `<div class="cols">${left}<div class="col">${grid}${sum}
      <div class="mrow"><button class="tbtn" data-act="finish"${round ? "" : " disabled"}>Finish round</button></div></div></div>`;
  }
  // the card: rows of up to 9 holes, each with its subtotal. With sel, the
  // cells are buttons that pick the hole; without, it's for looking only.
  function cardGrid(s, pars, nos, sel) {
    const n = s.length, tag = sel == null ? "div" : "button";
    let grid = "";
    for (let a = 0; a < n; a += 9) {
      const idx = [...Array(Math.min(9, n - a)).keys()].map(k => a + k);
      const sub = idx.reduce((t, i) => t + (s[i] || 0), 0), subPar = idx.reduce((t, i) => t + (pars[i] || 0), 0);
      const label = n <= 9 ? "Tot" : a === 0 ? "Out" : "In";
      grid += `<div class="cardrow">${idx.map(i => `<${tag} class="cell${i === sel ? " sel" : ""}"${sel == null ? "" : ` data-act="sel" data-arg="${i}"`}>
          <span class="ch">${nos[i] ?? i + 1}</span><span class="cp">${pars[i] ?? ""}</span>
          <span class="cs ${cls(s[i], pars[i])}">${s[i] ?? ""}</span></${tag}>`).join("")}
        <div class="cell sum"><span class="ch">${label}</span><span class="cp">${subPar || ""}</span><span class="cs">${sub || ""}</span></div></div>`;
    }
    return grid;
  }

  // ---- past rounds -----------------------------------------------------------------
  const roundName = r => r.name || (courses.find(c => c.id === r.course) || {}).name || r.course;
  // pars saved with the round; older rounds from this course use today's card
  const roundPars = r => (Array.isArray(r.par) ? r.par
    : r.course === current && r.scores.length === N ? A.holes.map((h, i) => par(i)) : r.scores.map(() => null));
  function roundSum(r) {
    const pars = roundPars(r);
    const played = r.scores.map((v, i) => i).filter(i => r.scores[i] != null);
    const gross = played.reduce((t, i) => t + r.scores[i], 0);
    const toPar = played.every(i => pars[i]) ? played.reduce((t, i) => t + r.scores[i] - pars[i], 0) : null;
    return { played: played.length, gross, toPar };
  }
  const fmtDate = iso => {
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  };
  function historyHtml() {
    if (!history) return `<p class="note">Loading…</p>`;
    if (!history.length) return `<p class="note">No finished rounds yet. Finish a round on the Score screen and it's kept here.</p>`;
    const pages = Math.ceil(history.length / HIST_PAGE);
    const items = history.slice(histPage * HIST_PAGE, (histPage + 1) * HIST_PAGE).map((r, k) => {
      const i = histPage * HIST_PAGE + k, t = roundSum(r), n = r.scores.length;
      return `<button class="item${i === histSel ? " on" : ""}" data-act="hist" data-arg="${i}">
        <span class="itemname"><span class="grow">${esc(roundName(r))}</span><span class="itemscore">${t.gross}</span></span>
        <span class="itemsub">${fmtDate(r.started)} · ${t.played === n ? `${n} holes` : `${t.played} of ${n}`}${t.toPar != null ? ` · ${fmtPar(t.toPar)}` : ""}</span></button>`;
    }).join("");
    const r = history[histSel], t = roundSum(r), pars = roundPars(r);
    const nos = r.course === current ? A.holes.map(h => h.no) : [];
    const kv = (k, v) => `<div><span>${k}</span><b>${v}</b></div>`;
    return `<div class="cols">
      <div class="col histlist"><div class="list">${items}</div>
        <div class="mrow"><button class="tbtn" data-act="histpage" data-arg="${histPage - 1}"${histPage ? "" : " disabled"}>‹ Newer</button>
          <span class="pageno">${histPage + 1} / ${pages}</span>
          <button class="tbtn" data-act="histpage" data-arg="${histPage + 1}"${histPage < pages - 1 ? "" : " disabled"}>Older ›</button></div></div>
      <div class="col"><div class="histhead"><b>${esc(roundName(r))}</b>
          <span>${fmtDate(r.started)}${r.tee ? ` · ${esc(r.tee)} tees` : ""}${r.hcp != null ? ` · HCP ${fmtHcp(r.hcp)}` : ""}</span></div>
        ${cardGrid(r.scores, pars, nos, null)}
        <div class="totals">${kv("Gross", t.played ? t.gross : "–")}${kv("To par", t.toPar != null && t.played ? fmtPar(t.toPar) : "–")}${kv("Holes", t.played)}</div>
      </div></div>`;
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
    const R = remote || { state: "off" };
    const [rstate, rdot] = {
      off: ["Not paired", "off"], nohotspot: ["Hotspot not running", "bad"], waiting: ["Waiting for the remote", "weak"],
      linked: [R.mock ? "Mock (testing)" : "Connected", "ok"], lost: ["No contact", "bad"],
    }[R.state] || ["Unknown", "off"];
    const rf = R.fix;
    const rgps = !rf ? "No fix" : `${rf.q === 2 ? "DGPS" : "Fix"}${rf.sats != null ? `, ${rf.sats} sats` : ""}${rf.acc != null ? `, ±${Math.round(rf.acc * A.YD)} yd` : ""}`;
    const rgap = rf && fresh ? `${Math.round(distYd(f, rf))} yd` : "–";
    const rseen = R.seen ? `${Math.max(0, Math.round(R.t - R.seen))} s ago` : "Never";
    const remoteCard = R.state === "off" || R.state === "nohotspot"
      ? `${row("Link", rstate)}<p class="note">Set up the hotspot and pairing key with pi/hotspot.sh (see remote/README.md).</p>`
      : `${row("Link", rstate)}${row("Last seen", rseen)}${row("Remote GPS", rgps)}${row("From cart", rgap)}${row("Battery", R.bat != null ? `${R.bat}%` : "–")}`;
    const T = A.trolley();
    const trolley = T ? `<div class="card"><div class="cardhead"><i class="sdot ${T.lost ? "bad" : "ok"}"></i>Trolley</div>
      ${row("Link", T.lost ? "No contact" : T.mock ? "Mock (testing)" : "Connected")}
      ${T.lost ? "" : row("Doing", esc(T.msg)) + row("Battery", `${T.battery}%`)}</div>` : "";
    return `<div class="cols">
      <div class="col"><div class="card"><div class="cardhead"><i class="sdot ${dot}"></i>GPS</div>
        ${row("Status", state)}${row("Satellites", sats)}${row("Accuracy", acc)}${row("Last fix", age)}${row("Position", pos)}
      </div></div>
      <div class="col"><div class="card"><div class="cardhead"><i class="sdot ${rdot}"></i>Remote control</div>
        ${remoteCard}</div>
        ${trolley}
        <div class="mrow"><button class="tbtn" data-act="btnbag">My Bag</button>
          <button class="tbtn" data-act="touch">Touch calibration</button></div>
      </div></div>`;
  }

  // ---- my bag and the club suggestion ---------------------------------------------------
  // the golfer's own clubs: any in the bag that aren't in CLUBS, longest first
  const customClubs = () => Object.keys(bag.clubs).filter(c => !CLUBS.includes(c))
    .sort((a, b) => bag.clubs[b] - bag.clubs[a]);
  function bagHtml() {
    const custom = customClubs();
    const tiles = CLUBS.concat(custom).map(c => {
      const y = bag.clubs[c];
      return `<button class="club${y ? " on" : ""}" data-act="club" data-arg="${esc(c)}">
        <span class="clubname">${esc(c)}</span><span class="clubyd">${y ? `${y} yds` : "+ Add"}</span></button>`;
    }).join("");
    const add = custom.length < MAX_CUSTOM
      ? `<button class="club add" data-act="custom"><span class="clubname">+ Custom</span><span class="clubyd">Name your own</span></button>` : "";
    return `<div class="clubgrid">${tiles}${add}</div>`;
  }

  // on-screen keyboard for a custom club's name (the kiosk has no keyboard).
  // Each word starts with a capital; Shift makes the next letter a capital too.
  let nameBuf = "", nameShift = false;
  function nameKeyboard() {
    const rows = ["1234567890", "QWERTYUIOP", "ASDFGHJKL°", "ZXCVBNM-"];
    const keyBtn = k => `<button class="tbtn" data-act="nkey" data-arg="${esc(k)}">${esc(k)}</button>`;
    modal(`<h2>Custom club name</h2>
      <div class="padval namebuf">${esc(nameBuf) || "&nbsp;"}</div>
      <div class="kbd">${rows.map((r, i) => `<div class="kbdrow">${i === 3 ? `<button class="tbtn${nameShift ? " on" : ""}" data-act="nshift">⇧</button>` : ""}${[...r].map(keyBtn).join("")}${i === 3 ? `<button class="tbtn" data-act="nkey" data-arg="⌫">⌫</button>` : ""}</div>`).join("")}
        <div class="kbdrow"><button class="tbtn space" data-act="nkey" data-arg=" ">Space</button></div></div>
      <div class="mrow"><button class="tbtn" data-act="close">Cancel</button>
        <button class="tbtn go" data-act="nnext"${nameBuf.trim() ? "" : " disabled"}>Next: yards</button></div>`, true);
    $("modalbox").classList.add("kbdbox");
  }
  function nameKey(k) {
    if (k === "⌫") nameBuf = nameBuf.slice(0, -1);
    else if (nameBuf.length < NAME_LEN && !(k === " " && (!nameBuf || nameBuf.endsWith(" ")))) {
      const wordStart = !nameBuf || nameBuf.endsWith(" ");
      nameBuf += wordStart || nameShift ? k : k.toLowerCase();
      nameShift = false;
    }
    nameKeyboard();
  }
  function nameDone() {
    const name = nameBuf.trim();
    const taken = CLUBS.concat(Object.keys(bag.clubs)).find(c => c.toLowerCase() === name.toLowerCase());
    if (taken) return toast(`${taken} is already in the list: tap it to set its yards.`);
    clubPad(name);
  }
  function saveBag(clubs) {
    api("/api/bag", { clubs }).then(b => { bag = b; closeModal(); draw(); suggestClub(); }, e => toast(String(e)));
  }
  // the shortest club in the bag that carries the distance (else the longest)
  function clubFor(yds) {
    const list = Object.entries(bag.clubs).sort((a, b) => a[1] - b[1]);
    if (!list.length || !(yds >= CLUB_MIN_YD)) return null;
    return list.find(([, c]) => c >= yds - CLUB_SLACK_YD) || list[list.length - 1];
  }
  function suggestClub() {
    const c = clubFor(A.toPin()), el = $("club");
    el.hidden = !c;
    if (c) el.innerHTML = `${esc(c[0])} <span>· ${c[1]}</span>`;
  }

  // ---- keypad: handicap index, or a club's carry yards -------------------------------------
  let pad = "";
  let padPlus = false;
  let padClub = null;          // the club being set, or null for the handicap
  function keypad() {
    const h = round ? round.hcp : player.hcp;
    padClub = null;
    padPlus = h != null && h < 0;
    pad = h == null ? "" : Math.abs(h).toFixed(1).replace(/\.0$/, "");
    drawPad();
  }
  function clubPad(c) {
    padClub = c;
    pad = bag.clubs[c] ? String(bag.clubs[c]) : "";
    drawPad();
  }
  function drawPad() {
    if (padClub) {
      const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "⌫"];
      return modal(`<h2>${esc(padClub)}: carry yards</h2>
        <div class="padval">${pad || "&nbsp;"}</div>
        <div class="pad">${keys.map(k => `<button class="tbtn" data-act="key" data-arg="${k}">${k}</button>`).join("")}</div>
        <div class="mrow"><button class="tbtn" data-act="club-remove"${bag.clubs[padClub] ? "" : " disabled"}>Not in bag</button>
          <button class="tbtn" data-act="close">Cancel</button>
          <button class="tbtn go" data-act="club-save">Save</button></div>`, true);
    }
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
    else if (padClub) { if (pad.length < 3 && (pad || k !== "0")) pad += k; }
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
      case "btnbag": return show("bag");
      case "history": return show("history");
      case "hist": histSel = +arg; return draw();
      case "histpage": histPage = +arg; histSel = histPage * HIST_PAGE; return draw();
      case "club": return clubPad(arg);
      case "custom": nameBuf = ""; nameShift = false; return nameKeyboard();
      case "nkey": return nameKey(arg);
      case "nshift": nameShift = !nameShift; return nameKeyboard();
      case "nnext": return nameDone();
      case "club-remove": { const c = { ...bag.clubs }; delete c[padClub]; return saveBag(c); }
      case "club-save": {
        const y = parseInt(pad, 10);
        if (!(y >= 1 && y <= 400)) return toast("Carry must be 1 to 400 yards.");
        return saveBag({ ...bag.clubs, [padClub]: y });
      }
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

  A.hooks.render = () => { badge(); suggestClub(); };
  setInterval(() => {
    if (open === "settings") api("/api/remote").then(r => { remote = r; }, () => { remote = null; });
    if (open === "settings" || open === "menu") draw();
  }, 1000);

  // ---- start ---------------------------------------------------------------------------
  Promise.all([
    api("/api/courses").catch(() => null),
    api("/api/player").catch(() => null),
    api("/api/round").catch(() => null),
    api("/api/bag").catch(() => null),
    fetch("scorecard.json").then(r => (r.ok ? r.json() : null)).catch(() => null),
  ]).then(([cs, p, r, b, sc]) => {
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
    if (b) bag = b;
    badge();
    suggestClub();
    const f = A.fix();
    if (f) A.hooks.fix(f);
  });
})();
