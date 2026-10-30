// Offline golf cart GPS map. Draws window.COURSE (from course.js) on a canvas,
// one hole at a time with the tee at the bottom and the green at the top.
// GPS comes from gps_server.py's /gps event stream (gpsd on the Pi).
(function () {
  "use strict";
  const C = window.COURSE;
  document.title = `${C.name} GPS`;
  const YD = 1.09361;              // yards per meter
  // (a course.js can set tighter ones: the office test course's holes are only 40-75 yd)
  const ADVANCE_M = C.advance_m || 25; // this close to the next tee -> offer to switch to that hole
  const STALE_MS = 6000;           // no fix for this long -> "GPS lost"
  const OFF_COURSE_M = 2000;
  const ARCS_YD = [100, 150, 200]; // layup arcs measured from the green center
  const TOUR_SPEED = 2.5;          // flyover plays this many times faster than the KML tour
  const TOUR_ZOOM = 3.2;           // screen height in meters = LookAt range x this
  const AUTO_FLYOVER = true;       // play the flyover whenever the hole changes
  const VIDEO_RATE = 2.0;          // recorded Google Earth clips play this many times faster
  const LIVE3D_BACK_M = 45;        // 3D view: camera this far behind the cart (or tee)...
  const LIVE3D_UP_M = 28;          // ...and this high, looking toward the pin
  const LIVE3D_AIM = 0.7;          // looking at this fraction of the way to the pin

  // ---- projection: lat/lon -> local meters (east, north) around the course --
  const ref = C.outline.length ? C.outline : C.holes[0].line;
  const lat0 = ref.reduce((s, p) => s + p[0], 0) / ref.length;
  const lon0 = ref.reduce((s, p) => s + p[1], 0) / ref.length;
  const KX = 111320 * Math.cos(lat0 * Math.PI / 180), KY = 110540;
  const proj = p => ({ x: (p[1] - lon0) * KX, y: (p[0] - lat0) * KY });
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  const centroid = pts => ({ x: pts.reduce((s, p) => s + p.x, 0) / pts.length,
                             y: pts.reduce((s, p) => s + p.y, 0) / pts.length });

  const DRAW_ORDER = ["fairway", "rough", "water", "bunker", "tee", "green", "building", "cartpath"];
  const STYLE = {
    fairway:  { fill: "#5fa04e" },
    rough:    { fill: "#4a8740" },
    water:    { fill: "#3b7fc4", stroke: "#2a5f96" },
    bunker:   { fill: "#eadfa6", stroke: "#c9b974" },
    tee:      { fill: "#8fd06f", stroke: "#e9f5e2" },
    green:    { fill: "#7fdc69", stroke: "#c8f2bb" },
    building: { fill: "#8b6d4f" },
    cartpath: { line: "#a7a7a0", width: 3 },
  };
  const features = C.features.map(f => ({
    layer: f.layer, kind: f.kind,
    rings: f.kind === "poly" ? f.rings.map(r => r.map(proj)) : null,
    pts: f.kind === "line" ? f.pts.map(proj) : null,
  })).sort((a, b) => DRAW_ORDER.indexOf(a.layer) - DRAW_ORDER.indexOf(b.layer));
  const outline = C.outline.map(proj);
  const track = (C.track || []).map(proj);
  const courseCenter = { x: 0, y: 0 };

  // Aerial photo tiles (app/aerial/, from tools/fetch_aerial.py). Each tile is
  // linear in lat/lon, like proj(), so it maps on with one affine transform.
  // Until they load (or if they're missing) the map uses the drawn shapes.
  const aerial = (window.AERIAL ? window.AERIAL.tiles : []).map(t => {
    const tile = { img: new Image(), ready: false, w: t.w, h: t.h,
                   x0: (t.west - lon0) * KX, y0: (t.north - lat0) * KY,
                   mx: (t.east - t.west) * KX / t.w, my: (t.north - t.south) * KY / t.h };
    tile.img.onload = () => { tile.ready = true; if (view && !flight && !videoOn) draw(); };
    tile.img.src = t.src;
    return tile;
  });
  const aerialReady = () => aerial.length > 0 && aerial.every(t => t.ready);

  const holes = C.holes.map(h => {
    const line = h.line.map(proj);
    const green = h.green ? h.green.map(proj) : null;
    const tees = h.tees.length ? h.tees.map(proj) : [line[0]];
    const pin = h.pin ? proj(h.pin) : green ? centroid(green) : line[line.length - 1];
    const tour = (h.tour || []).map(t => ({ ...t, cam: lookAtCam(t) }));
    return { ...h, line, green, tees, pin, targets: (h.targets || []).map(proj), tour,
             teesBack: tees.slice().sort((a, b) => dist(b, pin) - dist(a, pin)) }; // farthest first
  });

  // ---- tees ------------------------------------------------------------------
  // The map has each hole's tee boxes (from OSM) but not which set they belong
  // to. A tee set picks the box at its place in back-to-front order: frac 0 is
  // the farthest back, 1 the farthest forward. Yards come from the course's
  // scorecard.json when there is one (menus.js), else from the map.
  let teeSet = null;     // {name, frac, yards: [per hole] or null}
  function teePoint(h, frac = teeSet ? teeSet.frac : null) {
    if (frac == null) return h.line[0];
    return h.teesBack[Math.round(frac * (h.teesBack.length - 1))];
  }
  // hole length from a tee box: the mapped yards, adjusted by how much
  // nearer to or farther from the pin this box is than the hole line's start
  // (straight-line, so boxes off to the side of a dogleg still sort back to front)
  const teeYards = (h, frac) => Math.round(h.yards + (dist(teePoint(h, frac), h.pin) - dist(h.line[0], h.pin)) * YD);
  function mapTeeSets() {
    const n = Math.min(3, Math.max(...holes.map(h => h.teesBack.length)));
    const names = [["Tee"], ["Back", "Forward"], ["Back", "Middle", "Forward"]][n - 1];
    return names.map((name, k) => {
      const frac = n === 1 ? 0 : k / (n - 1);
      return { name, frac, yards: holes.map(h => teeYards(h, frac)), est: true };
    });
  }

  // ---- state -----------------------------------------------------------------
  let idx = 0;
  let fix = null;        // {p: {x,y}, acc, t}
  let rx = null;         // receiver status from /gps/status: {gpsd, device, mode, seen, used} or {sim}
  let gotFirstFix = false;
  let prompt = null;      // hole index the "Switch to hole N?" banner is offering, or null
  let declined = null;   // hole index the golfer said "Not now" to, until the cart leaves its tee
  let tap = null;        // world point the user tapped
  let cam = null;        // what's on screen now
  let view = null;       // transform for cam
  let flight = null;     // running 2D flyover: {legs, i, t0}
  let videoOn = false;   // a recorded flyover clip is loading or playing
  let videoToken = 0;    // bumps on every start/stop so stale video events are ignored
  let trolley = null;    // latest trolley status from the server, or null if there's no trolley
  let trolleyAt = 0;     // when that status arrived (ms)
  let pending = null;    // command waiting for its confirm tap: {body, timer}

  const $ = id => document.getElementById(id);
  const canvas = $("map"), ctx = canvas.getContext("2d");
  const video = $("video");
  const map3d = $("map3d"), ui3d = $("map3dui"), uctx = ui3d.getContext("2d");
  let v3d = null;        // View3D, once its data has loaded
  let mode3d = null;     // null, "fly" (3D flyover) or "live" (3D view from the cart)
  let flight3d = null;   // running 3D flyover: {legs, i, from, t0}

  // ---- camera: world point at an anchor on screen, heading = bearing of "up" --
  // cam = {c: {x,y}, hdg: radians clockwise from north, s: px per meter, ay: 0..1}
  function makeView(cam) {
    const W = canvas.width, H = canvas.height;
    const ux = Math.sin(cam.hdg), uy = Math.cos(cam.hdg); // up the screen
    const rx = uy, ry = -ux;                             // right on screen
    const ax = W / 2, ay = H * cam.ay, s = cam.s, c = cam.c;
    return {
      s,
      // world -> screen as a canvas matrix [a, b, c, d, e, f]
      m: [s * rx, -s * ux, s * ry, -s * uy,
          ax - s * (rx * c.x + ry * c.y), ay + s * (ux * c.x + uy * c.y)],
      toScreen(p) {
        const dx = p.x - c.x, dy = p.y - c.y;
        return { x: ax + (dx * rx + dy * ry) * s, y: ay - (dx * ux + dy * uy) * s };
      },
      toWorld(sx, sy) {
        const r = (sx - ax) / s, f = -(sy - ay) / s;
        return { x: c.x + r * rx + f * ux, y: c.y + r * ry + f * uy };
      },
    };
  }

  // Whole hole, tee at the bottom and pin at the top (above the trolley bar, if shown).
  function fitCam(h) {
    const W = canvas.width, o = h.line[0];
    const bar = $("trolleybar");
    const inset = bar && !bar.hidden ? bar.offsetHeight + 16 : 0; // floating dock + its margins
    const H = canvas.height - inset;
    const hdg = Math.atan2(h.pin.x - o.x, h.pin.y - o.y);
    const local = makeView({ c: o, hdg, s: 1, ay: 0 });
    const pts = h.line.concat(h.green || [], h.tees, h.targets).map(p => local.toScreen(p));
    const pad = 35; // meters of context around the hole
    const x0 = Math.min(...pts.map(p => p.x)) - pad, x1 = Math.max(...pts.map(p => p.x)) + pad;
    const y0 = Math.min(...pts.map(p => p.y)) - pad, y1 = Math.max(...pts.map(p => p.y)) + pad;
    const s = Math.min(W / (x1 - x0), H / (y1 - y0));
    return { c: local.toWorld((x0 + x1) / 2, (y0 + y1) / 2), hdg, s, ay: H / 2 / canvas.height };
  }

  // Google Earth LookAt -> flat camera. Earth's tilted view sees far ahead of the
  // look-at point, so put that point low on screen and zoom out to match.
  function lookAtCam(t) {
    return { c: proj([t.lat, t.lon]), hdg: t.heading * Math.PI / 180,
             s: () => canvas.height / (t.range * TOUR_ZOOM), ay: 0.68 };
  }

  function setCam(c) {
    cam = { ...c, s: typeof c.s === "function" ? c.s() : c.s };
    // a 0x0 canvas (hidden window) gives zoom 0, and zoom lerps turn that into NaN
    if (!(cam.s > 0 && isFinite(cam.s) && isFinite(cam.c.x) && isFinite(cam.hdg))) {
      flight = null;
      $("flyoverlabel").textContent = "Flyover";
      cam = fitCam(holes[idx]);
    }
    view = makeView(cam);
  }

  // ---- flyover ---------------------------------------------------------------------
  function lerpCam(a, b, k) {
    let dh = (b.hdg - a.hdg) % (2 * Math.PI);
    if (dh > Math.PI) dh -= 2 * Math.PI;
    if (dh < -Math.PI) dh += 2 * Math.PI;
    return {
      c: { x: a.c.x + (b.c.x - a.c.x) * k, y: a.c.y + (b.c.y - a.c.y) * k },
      hdg: a.hdg + dh * k,
      s: a.s * Math.pow(b.s / a.s, k), // zoom evenly in log space
      ay: a.ay + (b.ay - a.ay) * k,
    };
  }
  const ease = k => k * k * (3 - 2 * k);
  const solid = c => ({ ...c, s: typeof c.s === "function" ? c.s() : c.s });

  // Recorded Google Earth clip (app/flyovers/holeNN.mp4) if there is one,
  // otherwise the 2D replay of the KML tour.
  function startFlyover() {
    stopVideo();
    if (!(window.FLYOVERS || []).includes(holes[idx].no)) return startTour(); // no clip for this hole
    const token = ++videoToken;
    const fallback = () => { if (token === videoToken) { stopVideo(); startTour(); } };
    video.onerror = fallback;
    video.onplaying = () => { if (token === videoToken) video.hidden = false; };
    video.onended = () => { if (token === videoToken) stopFlyover(); };
    video.src = `flyovers/hole${String(holes[idx].no).padStart(2, "0")}.mp4`;
    video.defaultPlaybackRate = video.playbackRate = VIDEO_RATE;
    $("flyoverlabel").textContent = "Skip";
    videoOn = true;
    video.play().catch(fallback);
  }

  function stopVideo() {
    videoToken++;
    videoOn = false;
    video.hidden = true;
    video.pause();
    video.removeAttribute("src");
    video.load(); // release the decoder
  }

  function start2d() {
    const h = holes[idx];
    if (!h.tour.length) return stopFlyover();
    // legs: fly from where the camera is to each LookAt, hold, then back to the overview
    const legs = h.tour.map(t => ({ to: t.cam, dur: t.dur / TOUR_SPEED, hold: t.hold / TOUR_SPEED }));
    legs.push({ to: fitCam(h), dur: 1.5, hold: 0 });
    flight = { legs, i: 0, from: solid(cam), t0: performance.now() };
    $("flyoverlabel").textContent = "Skip";
    requestAnimationFrame(fly);
  }

  // the KML tour: in 3D over the lidar ground if this screen can, else flat
  function startTour() { return v3d ? start3d() : start2d(); }

  function stopFlyover() {
    flight = null;
    if (videoOn) stopVideo();
    if (mode3d === "fly") { flight3d = null; show3d(null); }
    $("flyoverlabel").textContent = "Flyover";
    setCam(fitCam(holes[idx]));
    render();
  }

  function fly(now) {
    if (!flight) return;
    const leg = flight.legs[flight.i], to = solid(leg.to);
    const t = (now - flight.t0) / 1000;
    if (t >= leg.dur + leg.hold) {
      flight.i++;
      flight.from = to;
      flight.t0 = now;
      if (flight.i >= flight.legs.length) return stopFlyover();
    } else {
      setCam(lerpCam(flight.from, to, ease(Math.min(t / (leg.dur || 1e-3), 1))));
      draw();
    }
    requestAnimationFrame(fly);
  }

  // ---- 3D ----------------------------------------------------------------------------
  // cam3 = {c: {x,y} look-at point, hdg: radians, tilt: degrees from straight down, range: m}
  const tourCam3 = t => ({ c: proj([t.lat, t.lon]), hdg: t.heading * Math.PI / 180, tilt: t.tilt, range: t.range });

  function overviewCam3(h) {
    const o = h.line[0], len = dist(o, h.pin);
    return { c: { x: (o.x * 2 + h.pin.x * 3) / 5, y: (o.y * 2 + h.pin.y * 3) / 5 },
             hdg: Math.atan2(h.pin.x - o.x, h.pin.y - o.y), tilt: 58, range: len * 0.9 + 80 };
  }

  function lerpCam3(a, b, k) {
    let dh = (b.hdg - a.hdg) % (2 * Math.PI);
    if (dh > Math.PI) dh -= 2 * Math.PI;
    if (dh < -Math.PI) dh += 2 * Math.PI;
    return { c: { x: a.c.x + (b.c.x - a.c.x) * k, y: a.c.y + (b.c.y - a.c.y) * k }, hdg: a.hdg + dh * k,
             tilt: a.tilt + (b.tilt - a.tilt) * k, range: a.range * Math.pow(b.range / a.range, k) };
  }

  // Google Earth LookAt: the camera sits `range` from the look-at point, tilted
  // from straight down, on the far side of the heading.
  function setCam3(c) {
    const t = { x: c.c.x, y: c.c.y, z: v3d.heightAt(c.c) };
    const tl = c.tilt * Math.PI / 180, r = c.range;
    v3d.setView({ x: t.x - r * Math.sin(tl) * Math.sin(c.hdg), y: t.y - r * Math.sin(tl) * Math.cos(c.hdg),
                  z: t.z + r * Math.cos(tl) }, t);
  }

  function show3d(mode) {
    mode3d = mode;
    map3d.hidden = ui3d.hidden = !mode;
    $("view3dlabel").textContent = mode === "live" ? "2D" : "3D";
    if (!mode) uctx.clearRect(0, 0, ui3d.width, ui3d.height);
  }

  function start3d() {
    const h = holes[idx];
    if (!h.tour.length) return stopFlyover();
    const legs = [{ to: overviewCam3(h), dur: 0, hold: 0.6 }]
      .concat(h.tour.map(t => ({ to: tourCam3(t), dur: t.dur / TOUR_SPEED, hold: t.hold / TOUR_SPEED })),
              [{ to: overviewCam3(h), dur: 1.5, hold: 0.3 }]);
    flight3d = { legs, i: 0, from: overviewCam3(h), t0: performance.now() };
    show3d("fly");
    $("flyoverlabel").textContent = "Skip";
    requestAnimationFrame(fly3d);
  }

  function fly3d(now) {
    if (!flight3d || mode3d !== "fly") return;
    const leg = flight3d.legs[flight3d.i];
    if ((now - flight3d.t0) / 1000 >= leg.dur + leg.hold) {
      flight3d.i++;
      flight3d.from = leg.to;
      flight3d.t0 = now;
      if (flight3d.i >= flight3d.legs.length) return stopFlyover();
    }
    const cur = flight3d.legs[flight3d.i];
    setCam3(lerpCam3(flight3d.from, cur.to, ease(Math.min((now - flight3d.t0) / 1000 / (cur.dur || 1e-3), 1))));
    v3d.render();
    drawUi3d(holes[idx], false);
    requestAnimationFrame(fly3d);
  }

  // From behind the cart (or the tee), up in the air, looking at the pin.
  function drawLive3d() {
    const h = holes[idx], o = origin();
    const d = Math.max(dist(o, h.pin), 1), ux = (h.pin.x - o.x) / d, uy = (h.pin.y - o.y) / d;
    const eye = { x: o.x - ux * LIVE3D_BACK_M, y: o.y - uy * LIVE3D_BACK_M };
    eye.z = v3d.heightAt(o) + LIVE3D_UP_M;
    // aim a little short of the pin: the hole fills the view and the flag sits above the trolley dock
    const aim = { x: o.x + (h.pin.x - o.x) * LIVE3D_AIM, y: o.y + (h.pin.y - o.y) * LIVE3D_AIM };
    v3d.setView(eye, { ...aim, z: v3d.heightAt(aim) });
    v3d.render();
    drawUi3d(h, true);
  }

  function toggle3d() {
    if (mode3d === "live") { show3d(null); return render(); }
    stopFlyover();
    show3d("live");
    drawLive3d();
  }

  // Markers over the 3D picture: hole line, aim targets with yards, the flag,
  // the cart and the trolley. Drawn flat on top; hills don't hide them.
  function drawUi3d(h, yards) {
    uctx.clearRect(0, 0, ui3d.width, ui3d.height);
    const P = p => v3d.project(p);
    const drape = pts => { // project a ground line every few meters so it follows the hills
      const out = [];
      for (let i = 0; i < pts.length - 1; i++) {
        const a = pts[i], b = pts[i + 1], n = Math.max(1, Math.ceil(dist(a, b) / 4));
        for (let k = 0; k < n; k++) out.push(P({ x: a.x + (b.x - a.x) * k / n, y: a.y + (b.y - a.y) * k / n }));
      }
      out.push(P(pts[pts.length - 1]));
      return out;
    };
    const line = (pts, width, color, dash) => {
      uctx.beginPath();
      let pen = false;
      for (const s of pts) {
        if (!s) { pen = false; continue; }
        if (pen) uctx.lineTo(s.x, s.y); else uctx.moveTo(s.x, s.y);
        pen = true;
      }
      uctx.setLineDash(dash || []);
      uctx.lineWidth = width + 3; uctx.strokeStyle = "rgba(0,0,0,.45)"; uctx.stroke();
      uctx.lineWidth = width; uctx.strokeStyle = color; uctx.stroke();
      uctx.setLineDash([]);
    };
    const tag = (text, x, y) => {
      uctx.font = "800 15px Inter, sans-serif";
      const w = uctx.measureText(text).width + 14;
      uctx.beginPath();
      if (uctx.roundRect) uctx.roundRect(x - w / 2, y - 12, w, 24, 12); else uctx.rect(x - w / 2, y - 12, w, 24);
      uctx.fillStyle = "rgba(10,18,14,.8)"; uctx.fill();
      uctx.fillStyle = "#fff"; uctx.textAlign = "center"; uctx.textBaseline = "middle";
      uctx.fillText(text, x, y + 0.5);
    };

    line(drape(h.line), 2, "rgba(255,255,255,.9)", [9, 7]);
    const o = origin();
    for (const t of h.targets) {
      const s = P(t);
      if (!s) continue;
      uctx.beginPath(); uctx.arc(s.x, s.y, 7, 0, Math.PI * 2);
      uctx.lineWidth = 2.5; uctx.strokeStyle = "#fff"; uctx.stroke();
      if (yards) tag(String(yd(dist(o, t))), s.x, s.y - 22);
    }
    if (trolley) {
      if (trolley.path.length > 1) line(drape(trolley.path.map(proj)), 3, "#ffa53b", [10, 6]);
      const s = P(proj([trolley.lat, trolley.lon]));
      if (s) {
        uctx.beginPath(); uctx.arc(s.x, s.y, 8, 0, Math.PI * 2);
        uctx.fillStyle = trolley.state === "stopped" ? "#ff5a4e" : "#ffa53b"; uctx.fill();
        uctx.lineWidth = 2.5; uctx.strokeStyle = "#fff"; uctx.stroke();
      }
    }
    // the flag: true 2.4 m height when close, never smaller than a readable marker
    const base = P(h.pin), top = P({ x: h.pin.x, y: h.pin.y, z: v3d.heightAt(h.pin) + 2.4 });
    if (base && top) {
      const len = Math.max(base.y - top.y, 30);
      uctx.beginPath(); uctx.moveTo(base.x, base.y); uctx.lineTo(base.x, base.y - len);
      uctx.lineWidth = 5; uctx.strokeStyle = "rgba(0,0,0,.45)"; uctx.stroke();
      uctx.lineWidth = 2.5; uctx.strokeStyle = "#fff"; uctx.stroke();
      uctx.beginPath(); uctx.moveTo(base.x, base.y - len); uctx.lineTo(base.x + 18, base.y - len + 6);
      uctx.lineTo(base.x, base.y - len + 12); uctx.closePath(); uctx.fillStyle = "#ff4d4d"; uctx.fill();
      if (yards) tag(`${yd(dist(o, h.pin))} to pin`, base.x, base.y - len - 18);
    }
    if (yards && fix && Date.now() - fix.t < STALE_MS) {
      const s = P(fix.p);
      if (s) {
        uctx.beginPath(); uctx.arc(s.x, s.y, 8, 0, Math.PI * 2);
        uctx.fillStyle = "#2f8cff"; uctx.fill(); uctx.lineWidth = 3; uctx.strokeStyle = "#fff"; uctx.stroke();
      }
    }
  }

  function start3dData() {
    if (!window.View3D || !window.TERRAIN3D || !window.AERIAL) return;
    // the Pi's GPU gets every other height sample (4 m); a PC gets them all
    const step = /aarch64|arm/i.test(navigator.userAgent) ? 2 : 1;
    let v = null;
    try { v = window.View3D(map3d, { proj, aerial: window.AERIAL, terrain: window.TERRAIN3D, step }); } catch (e) { v = null; }
    if (!v) return;
    v.ready.then(() => {
      v3d = v;
      $("view3d").hidden = false;
    }).catch(() => {}); // no 3D data: keep the 2D map and flyover
  }

  // ---- yardages ----------------------------------------------------------------
  // Front/back = where the line from the player through the pin crosses the green edge.
  function greenDepth(from, h) {
    const c = h.pin;
    if (!h.green) return null;
    const dx = c.x - from.x, dy = c.y - from.y;
    const ts = [];
    const g = h.green;
    for (let i = 0; i < g.length; i++) {
      const a = g[i], b = g[(i + 1) % g.length];
      const ex = b.x - a.x, ey = b.y - a.y;
      const den = dx * ey - dy * ex;
      if (Math.abs(den) < 1e-9) continue;
      const t = ((a.x - from.x) * ey - (a.y - from.y) * ex) / den;
      const u = ((a.x - from.x) * dy - (a.y - from.y) * dx) / den;
      if (u >= 0 && u <= 1 && t >= 0) ts.push(t);
    }
    if (!ts.length) return null;
    const len = Math.hypot(dx, dy);
    return { front: Math.min(...ts) * len, back: Math.max(...ts) * len };
  }

  function origin() {
    // Measure from the cart when we have a fix on the course, else from the tee.
    if (fix && Date.now() - fix.t < STALE_MS && dist(fix.p, courseCenter) < OFF_COURSE_M) return fix.p;
    return teePoint(holes[idx]);
  }

  const yd = m => Math.round(m * YD);

  // ---- drawing -----------------------------------------------------------------
  function path(pts, close) {
    ctx.beginPath();
    pts.forEach((p, i) => { const s = view.toScreen(p); i ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y); });
    if (close) ctx.closePath();
  }

  function drawAerial() {
    const [a, b, c, d, e, f] = view.m;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    for (const t of aerial) {
      // tile pixel (u, v) -> world (x0 + u*mx, y0 - v*my) -> screen
      ctx.setTransform(a * t.mx, b * t.mx, -c * t.my, -d * t.my,
                       a * t.x0 + c * t.y0 + e, b * t.x0 + d * t.y0 + f);
      ctx.drawImage(t.img, 0, 0, t.w + 1, t.h + 1); // +1 px overlap hides tile seams
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  // white line with a dark halo, so it reads on a busy photo
  function haloStroke(width, color, dash) {
    ctx.setLineDash(dash || []);
    ctx.strokeStyle = "rgba(0,0,0,.45)"; ctx.lineWidth = width + 3; ctx.stroke();
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.stroke();
    ctx.setLineDash([]);
  }

  function pill(text, x, y, bg) {
    ctx.font = "700 15px Inter, DejaVu Sans, Arial, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const w = ctx.measureText(text).width + 14;
    ctx.beginPath(); ctx.roundRect(x - w / 2, y - 11, w, 22, 11);
    ctx.fillStyle = bg || "rgba(10,18,14,.78)"; ctx.fill();
    ctx.fillStyle = "#fff"; ctx.fillText(text, x, y + 0.5);
  }

  function draw() {
    const h = holes[idx];
    ctx.fillStyle = "#13221a"; // beyond the photo / out of bounds
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (aerialReady()) {
      drawAerial();
      if (h.green) { path(h.green, true); ctx.strokeStyle = "rgba(255,255,255,.55)"; ctx.lineWidth = 1.5; ctx.stroke(); }
    } else {
      drawShapes();
    }
    drawOverlays(h);
    if (mode3d === "live") drawLive3d();
  }

  function drawShapes() {
    ctx.fillStyle = "#2f5a2a";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (outline.length) { path(outline, true); ctx.fillStyle = "#3d6b35"; ctx.fill(); }

    for (const f of features) {
      const st = STYLE[f.layer];
      if (f.kind === "poly") {
        ctx.beginPath();
        for (const ring of f.rings) {
          ring.forEach((p, i) => { const s = view.toScreen(p); i ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y); });
          ctx.closePath();
        }
        ctx.fillStyle = st.fill || st.line;
        ctx.fill("evenodd");
        if (st.stroke) { ctx.strokeStyle = st.stroke; ctx.lineWidth = 1.5; ctx.stroke(); }
      } else {
        path(f.pts, false);
        ctx.strokeStyle = st.line || st.fill; ctx.lineWidth = st.width || 2; ctx.stroke();
      }
    }

    // recorded cart route, faint
    if (track.length) {
      path(track, false);
      ctx.setLineDash([2, 6]); ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = 2; ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  function drawOverlays(h) {
    // layup arcs from the pin
    const pinS = view.toScreen(h.pin);
    for (const a of ARCS_YD) {
      const r = a / YD * view.s;
      ctx.beginPath(); ctx.arc(pinS.x, pinS.y, r, Math.PI * 0.64, Math.PI * 0.36, true);
      haloStroke(1.5, "rgba(255,255,255,.8)");
    }
    for (const a of ARCS_YD) {
      const r = a / YD * view.s;
      // labels on the left end of each arc; aim-target labels sit to the right
      pill(String(a), pinS.x + r * Math.cos(Math.PI * 0.64), pinS.y + r * Math.sin(Math.PI * 0.64),
           "rgba(10,18,14,.6)");
    }

    // centerline tee -> pin
    path(h.line, false);
    haloStroke(2, "rgba(255,255,255,.9)", [9, 7]);

    // aim targets from the KML, labeled with yards from the cart (or tee)
    const o = origin();
    for (const t of h.targets) {
      const s = view.toScreen(t);
      ctx.beginPath(); ctx.arc(s.x, s.y, 9, 0, Math.PI * 2);
      haloStroke(2.5, "#fff");
      ctx.beginPath(); ctx.arc(s.x, s.y, 2.5, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill();
      pill(String(yd(dist(o, t))), s.x + 34, s.y);
    }

    drawFlag(pinS);

    // tap measurement
    if (tap) {
      const oS = view.toScreen(o), tS = view.toScreen(tap);
      ctx.beginPath(); ctx.moveTo(oS.x, oS.y); ctx.lineTo(tS.x, tS.y); ctx.lineTo(pinS.x, pinS.y);
      haloStroke(3, "#ffd54a");
      ctx.beginPath(); ctx.arc(tS.x, tS.y, 9, 0, Math.PI * 2);
      ctx.fillStyle = "#ffd54a"; ctx.fill(); ctx.strokeStyle = "#1b1b1b"; ctx.lineWidth = 2.5; ctx.stroke();
    }

    // the golfer / cart GPS position
    if (fix && Date.now() - fix.t < STALE_MS) {
      const s = view.toScreen(fix.p);
      ctx.beginPath(); ctx.arc(s.x, s.y, Math.max((fix.acc || 0) * view.s, 16), 0, Math.PI * 2);
      ctx.fillStyle = "rgba(66,165,255,.22)"; ctx.fill();
      ctx.beginPath(); ctx.arc(s.x, s.y, 9, 0, Math.PI * 2);
      ctx.fillStyle = "#2f8cff"; ctx.fill(); ctx.strokeStyle = "#fff"; ctx.lineWidth = 3; ctx.stroke();
    }

    drawTrolley();
  }

  // Trolley: planned path, goal ring, and an arrow pointing where it's heading.
  function drawTrolley() {
    if (!trolley) return;
    const T = trolley;
    if (T.path.length > 1) {
      path(T.path.map(proj), false);
      haloStroke(4, "#ffa53b", [10, 6]);
    }
    if (T.goal) {
      const g = view.toScreen(proj(T.goal));
      ctx.beginPath(); ctx.arc(g.x, g.y, 11, 0, Math.PI * 2);
      haloStroke(4, "#ffa53b");
    }
    const s = view.toScreen(proj([T.lat, T.lon]));
    const a = T.heading * Math.PI / 180 - cam.hdg; // screen angle, 0 = up
    ctx.save();
    ctx.translate(s.x, s.y); ctx.rotate(a);
    ctx.beginPath(); ctx.moveTo(0, -15); ctx.lineTo(11, 11); ctx.lineTo(0, 5); ctx.lineTo(-11, 11); ctx.closePath();
    ctx.fillStyle = T.state === "stopped" ? "#ff5a4e" : "#ffa53b";
    ctx.fill(); ctx.strokeStyle = "#fff"; ctx.lineWidth = 2.5; ctx.stroke();
    ctx.restore();
  }

  function drawFlag(s) {
    ctx.beginPath(); ctx.ellipse(s.x + 3, s.y + 1, 9, 3.5, 0, 0, Math.PI * 2); // cup shadow
    ctx.fillStyle = "rgba(0,0,0,.35)"; ctx.fill();
    ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(s.x, s.y - 34);
    haloStroke(2.5, "#fff");
    ctx.beginPath(); ctx.moveTo(s.x, s.y - 34); ctx.lineTo(s.x + 20, s.y - 27); ctx.lineTo(s.x, s.y - 20);
    ctx.closePath(); ctx.fillStyle = "#ff4d4d"; ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,.4)"; ctx.lineWidth = 1; ctx.stroke();
    ctx.beginPath(); ctx.arc(s.x, s.y, 4, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill();
  }

  // ---- panel ---------------------------------------------------------------------
  function updatePanel() {
    const h = holes[idx];
    $("holeno").textContent = h.no;
    // a scorecard tee set can have its own par and stroke index per hole
    const par = (teeSet && teeSet.par && teeSet.par[idx]) || h.par;
    const si = (teeSet && teeSet.hcp && teeSet.hcp[idx]) || h.hcp;
    $("holepar").textContent = `PAR ${par ?? "?"}`;
    const yards = teeSet && teeSet.yards ? teeSet.yards[idx] : h.yards;
    $("holeyds").textContent = `${yards} yds` + (si ? ` · SI ${si}` : "");
    const o = origin();
    $("ycenter").textContent = yd(dist(o, h.pin));
    const d = greenDepth(o, h);
    $("yfront").textContent = d ? yd(d.front) : "--";
    $("yback").textContent = d ? yd(d.back) : "--";

    const info = $("tapinfo");
    if (tap) {
      info.hidden = false;
      info.innerHTML = `<div class="row">To point<b>${yd(dist(o, tap))}</b></div>` +
                       `<div class="row">Point to pin<b>${yd(dist(tap, h.pin))}</b></div>`;
    } else info.hidden = true;

    const dot = $("gpsdot"), txt = $("gpstext");
    if (!fix || Date.now() - fix.t > STALE_MS) {
      // say why there's no position: no receiver at all, or one still finding satellites
      if (rx && rx.device && !rx.sim) {
        const n = rx.seen || 0;
        dot.className = "weak"; txt.textContent = `Searching · ${n} sat${n === 1 ? "" : "s"} – from tee`; // one line at 800x480
      } else if (rx && !rx.sim) {
        dot.className = ""; txt.textContent = rx.gpsd ? "No GPS device – yards from tee" : "GPS service down – from tee";
      } else {
        dot.className = ""; txt.textContent = fix ? "GPS lost – yards from tee" : "No GPS – yards from tee";
      }
    } else if (dist(fix.p, courseCenter) > OFF_COURSE_M) {
      dot.className = "weak"; txt.textContent = "Off course – yards from tee";
    } else {
      const acc = fix.acc ? Math.round(fix.acc * YD) : null;
      dot.className = acc != null && acc > 10 ? "weak" : "ok";
      txt.textContent = "GPS" + (acc != null ? ` ±${acc} yd` : "") + (fix.sim ? " (sim)" : "");
    }
  }

  function render() { updatePanel(); updateTrolleyBar(); draw(); hooks.render(); }

  function setHole(i) {
    idx = (i + holes.length) % holes.length;
    tap = null;
    showPrompt(null);
    declined = null;
    flight = null;
    if (videoOn) stopVideo();
    if (mode3d === "fly") { flight3d = null; show3d(null); }
    setCam(fitCam(holes[idx]));
    render();
    if (AUTO_FLYOVER) startFlyover();
  }

  // ---- GPS -----------------------------------------------------------------------
  // within ADVANCE_M (times k) of any of the hole's tee boxes
  function nearTee(h, p, k = 1) { return h.tees.some(t => dist(t, p) < ADVANCE_M * k) || dist(h.line[0], p) < ADVANCE_M * k; }

  function onFix(lat, lon, acc, sim) {
    fix = { p: proj([lat, lon]), lat, lon, acc, t: Date.now(), sim };
    hooks.fix(fix);
    if (!gotFirstFix) {
      // first fix: jump to whichever hole's tee we're standing on, if any
      gotFirstFix = true;
      const i = holes.findIndex(h => nearTee(h, fix.p));
      if (i >= 0 && i !== idx) return setHole(i);
    }
    // at the next hole's tee: ask before switching (carts pass other tees,
    // and a golfer may wait there for the group ahead)
    const next = (idx + 1) % holes.length;
    if (nearTee(holes[next], fix.p)) {
      if (declined !== next) showPrompt(next);
    } else if (!nearTee(holes[next], fix.p, 2)) {
      showPrompt(null); // well clear of the tee: hide, and ask again next time
      declined = null;
    }
    render();
  }

  function showPrompt(i) {
    prompt = i;
    $("holeprompt").hidden = i == null;
    if (i != null) $("holeprompttext").innerHTML = `At the hole ${holes[i].no} tee<b>Switch to hole ${holes[i].no}?</b>`;
  }

  function startGps() {
    if (location.protocol.startsWith("http") && window.EventSource) {
      const es = new EventSource("/gps");
      es.onmessage = e => { const m = JSON.parse(e.data); onFix(m.lat, m.lon, m.acc, m.sim); };
      const st = new EventSource("/gps/status"); // receiver status, for the status line
      st.onmessage = e => { rx = JSON.parse(e.data); updatePanel(); };
      return; // EventSource reconnects on its own if the server restarts
    }
    if (navigator.geolocation) {
      navigator.geolocation.watchPosition(
        p => onFix(p.coords.latitude, p.coords.longitude, p.coords.accuracy, false),
        () => {}, { enableHighAccuracy: true, maximumAge: 1000 });
    }
  }

  // ---- trolley ---------------------------------------------------------------------
  // Present only when the server has one (/api/trolley answers). The server
  // checks every goal against the keep-out map; this side just asks.
  const TROLLEY_STALE_MS = 3000;   // no status for this long -> show "no contact"
  const CONFIRM_MS = 6000;         // an unconfirmed command is dropped after this

  function startTrolley() {
    if (!location.protocol.startsWith("http") || !window.EventSource) return;
    const es = new EventSource("/api/trolley");
    es.onmessage = e => {
      const first = !trolley;
      trolley = JSON.parse(e.data);
      trolleyAt = Date.now();
      if (first) {
        $("trolleybar").hidden = false;
        document.body.classList.add("has-trolley");
        if (!flight && !videoOn) setCam(fitCam(holes[idx])); // make room for the bar
      }
      updateTrolleyBar();
      if (!flight && !videoOn) draw();
    };
    // a 404 closes the stream for good (no trolley); other errors retry on their own
    setInterval(() => { if (trolley) post("heartbeat", {}); updateTrolleyBar(); }, 1000);
  }

  function post(what, body) {
    return fetch(`/api/trolley/${what}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Trolley-Client": "1" },
      body: JSON.stringify(body),
    }).then(r => r.json()).catch(() => ({ ok: false, msg: "No contact with the trolley." }));
  }

  let note = null; // short-lived reply from the server, shown instead of the status

  function updateTrolleyBar() {
    if (!trolley) return;
    const lost = Date.now() - trolleyAt > TROLLEY_STALE_MS;
    const T = trolley;
    let text = lost ? "No contact with the trolley"
      : T.msg + (T.eta_s != null ? ` · ${Math.max(1, Math.round(T.eta_s / 60))} min` : "") + ` · ${T.battery}%`;
    if (note && Date.now() < note.until) text = note.text;
    $("ttext").textContent = (T.mock ? "Mock trolley: " : "Trolley: ") + text;
    $("tdot").className = lost ? "lost" : T.state;
    $("tpoint").hidden = !tap;
    for (const b of document.querySelectorAll("#tbtns .tbtn:not(.stop)")) b.disabled = lost;
  }

  function say(text) { note = { text, until: Date.now() + 5000 }; updateTrolleyBar(); }

  const TARGET_NAMES = { next_tee: "to the next tee", green_side: "beside this green",
                         golfer: "to you", point: "to the marker" };

  function askToSend(target) {
    const body = { cmd: "goto", target, hole: holes[idx].no };
    if (target === "point") {
      if (!tap) return;
      Object.assign(body, toLatLon(tap));
    }
    cancelConfirm();
    $("confirmtext").textContent = `Send the trolley ${TARGET_NAMES[target]}?`;
    $("confirm").hidden = false;
    pending = { body, timer: setTimeout(cancelConfirm, CONFIRM_MS) };
  }

  function cancelConfirm() {
    if (pending) clearTimeout(pending.timer);
    pending = null;
    $("confirm").hidden = true;
  }

  function sendPending() {
    if (!pending) return;
    const body = pending.body;
    cancelConfirm();
    post("cmd", body).then(r => say(r.msg));
  }

  function stopTrolley() {
    cancelConfirm();
    post("cmd", { cmd: "stop" }).then(r => say(r.ok ? "Stopped." : r.msg));
  }

  // inverse of proj(), for sending tapped map points
  const toLatLon = p => ({ lat: lat0 + p.y / KY, lon: lon0 + p.x / KX });

  // ---- input ---------------------------------------------------------------------
  function resize() {
    if (!canvas.clientWidth || !canvas.clientHeight) return; // hidden; keep the last size
    canvas.width = canvas.clientWidth;
    canvas.height = canvas.clientHeight;
    map3d.width = ui3d.width = canvas.width;
    map3d.height = ui3d.height = canvas.height;
    if (v3d) v3d.resize();
    if (flight) return stopFlyover(); // the tour's legs were sized for the old screen
    setCam(fitCam(holes[idx]));
    render();
  }

  // Where a press really was, for jittery resistive screens: every sample while
  // the finger is down, minus the first 40 ms of contact (the noisiest), median.
  function steadyTap(el, onDown, fn) {
    let pts = null, t0 = 0;
    const add = e => {
      for (const c of (e.getCoalescedEvents && e.getCoalescedEvents().length ? e.getCoalescedEvents() : [e]))
        pts.push([c.clientX, c.clientY, e.timeStamp - t0]);
    };
    el.addEventListener("pointerdown", e => {
      if (onDown(e) === false) { pts = null; return; }
      pts = []; t0 = e.timeStamp; add(e);
      el.setPointerCapture(e.pointerId);
    });
    el.addEventListener("pointermove", e => { if (pts) add(e); });
    el.addEventListener("pointerup", e => {
      if (!pts) return;
      add(e);
      const settled = pts.filter(p => p[2] >= 40);
      const use = settled.length ? settled : pts;
      const med = k => use.map(p => p[k]).sort((a, b) => a - b)[use.length >> 1];
      pts = null;
      fn(med(0), med(1));
    });
    el.addEventListener("pointercancel", () => { pts = null; });
  }

  steadyTap(canvas, () => {
    if (flight) { stopFlyover(); return false; } // any touch skips the flyover
  }, (x, y) => {
    const r = canvas.getBoundingClientRect();
    const sx = (x - r.left) * canvas.width / r.width;
    const sy = (y - r.top) * canvas.height / r.height;
    const w = view.toWorld(sx, sy);
    // tapping the same marker again clears it
    tap = tap && dist(view.toScreen(tap), { x: sx, y: sy }) < 30 ? null : w;
    render();
  });
  // Buttons fire on release, wherever the finger ended up. A "click" is dropped
  // if the finger drifts while pressed, which resistive touch screens do all the time.
  function onTap(el, fn) {
    let down = false;
    el.addEventListener("pointerdown", e => { down = true; el.setPointerCapture(e.pointerId); });
    el.addEventListener("pointerup", () => { if (down && !el.disabled) fn(); down = false; });
    el.addEventListener("pointercancel", () => { down = false; });
  }
  onTap($("prev"), () => setHole(idx - 1));
  onTap($("next"), () => setHole(idx + 1));
  onTap($("flyover"), () => (flight || videoOn || mode3d === "fly" ? stopFlyover() : startFlyover()));
  onTap($("view3d"), toggle3d);
  ui3d.addEventListener("pointerdown", () => (mode3d === "fly" ? stopFlyover() : toggle3d())); // tap 3D to leave it
  video.addEventListener("pointerdown", () => stopFlyover()); // tap the clip to skip it
  $("tstop").addEventListener("pointerdown", stopTrolley);  // on touch-down: no waiting for release
  for (const b of document.querySelectorAll("#tbtns [data-target]")) {
    onTap(b, () => askToSend(b.dataset.target));
  }
  onTap($("confirmyes"), sendPending);
  onTap($("confirmno"), cancelConfirm);
  onTap($("holepromptgo"), () => { if (prompt != null) setHole(prompt); });
  onTap($("holepromptno"), () => { declined = prompt; showPrompt(null); });
  // hold the GPS status line for 3 s: the touch calibration page
  let holdTimer = null;
  $("status").addEventListener("pointerdown", () => { holdTimer = setTimeout(() => { location.href = "touchtest.html"; }, 3000); });
  for (const ev of ["pointerup", "pointercancel", "pointerleave"]) $("status").addEventListener(ev, () => clearTimeout(holdTimer));
  window.addEventListener("resize", resize);
  setInterval(updatePanel, 2000); // flips status to "GPS lost" when fixes stop
  document.addEventListener("contextmenu", e => e.preventDefault());

  // What menus.js (Menu, Score, Settings) reads and calls. It sets the hooks.
  const hooks = { render() {}, fix() {} };
  window.APP = {
    hooks, holes, YD, STALE_MS,
    hole: () => idx,
    setHole,
    fix: () => fix,
    rx: () => rx,
    trolley: () => (trolley && Date.now() - trolleyAt < TROLLEY_STALE_MS ? trolley : trolley && { lost: true }),
    mapTeeSets,
    stopTrolley: () => stopTrolley(),
    setTees(t) { teeSet = t; render(); },
  };

  canvas.width = map3d.width = ui3d.width = canvas.clientWidth || 550;
  canvas.height = map3d.height = ui3d.height = canvas.clientHeight || 480;
  start3dData();
  setHole(0);
  if (document.fonts) document.fonts.ready.then(() => { if (!flight && !videoOn) draw(); });
  startGps();
  startTrolley();
})();
