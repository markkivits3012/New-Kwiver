// 3D course view: lidar ground (terrain3d/, from tools/fetch_elevation.py)
// draped with the aerial photo (aerial/), drawn with plain WebGL.
//
//   const v = View3D(canvas, { proj, aerial: window.AERIAL, terrain: window.TERRAIN3D });
//   v.ready.then(...)            heightmaps and photos loaded
//   v.setView(eye, target)       points in proj() meters, z = meters above sea level
//   v.render()
//   v.project(p) -> {x, y} canvas px, or null if behind the camera
//   v.heightAt(p) -> ground height at {x, y}
// Returns null if the browser has no WebGL or there's no terrain data.
window.View3D = function (canvas, opts) {
  "use strict";
  const T = opts.terrain, A = opts.aerial, proj = opts.proj;
  if (!T || !A) return null;
  let gl = canvas.getContext("webgl2", { antialias: true });
  const gl2 = !!gl;
  if (!gl) gl = canvas.getContext("webgl", { antialias: true }) || canvas.getContext("experimental-webgl");
  if (!gl) return null;
  const uint32 = gl2 || !!gl.getExtension("OES_element_index_uint");
  const aniso = gl.getExtension("EXT_texture_filter_anisotropic");

  const HAZE = [0.78, 0.85, 0.9];
  const SUN = norm([-0.45, -0.55, 0.7]);   // from the southwest-ish, like a summer afternoon
  const FOV_H = 60 * Math.PI / 180;        // Google Earth's default view width

  // ---- shaders ---------------------------------------------------------------------
  const vs = `
    attribute vec3 aPos; attribute vec2 aUV; attribute vec3 aN;
    uniform mat4 uMVP; uniform vec3 uEye; uniform vec3 uSun;
    varying vec2 vUV; varying float vShade; varying float vDist;
    void main() {
      // relief shading, scaled so flat ground keeps the photo's own brightness
      float level = 0.4 + 0.6 * uSun.z;
      vShade = clamp((0.4 + 0.6 * max(dot(aN, uSun), 0.0)) / level, 0.55, 1.2);
      vUV = aUV;
      vDist = distance(aPos, uEye);
      gl_Position = uMVP * vec4(aPos, 1.0);
    }`;
  const fs = `
    precision mediump float;
    uniform sampler2D uTex; uniform vec3 uHaze; uniform vec2 uFog;
    varying vec2 vUV; varying float vShade; varying float vDist;
    void main() {
      vec3 c = texture2D(uTex, vUV).rgb * vShade;
      gl_FragColor = vec4(mix(c, uHaze, smoothstep(uFog.x, uFog.y, vDist)), 1.0);
    }`;
  const prog = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) { console.error(gl.getShaderInfoLog(s)); return null; }
    gl.attachShader(prog, s);
  }
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { console.error(gl.getProgramInfoLog(prog)); return null; }
  const loc = n => gl.getAttribLocation(prog, n), uni = n => gl.getUniformLocation(prog, n);
  const L = { pos: loc("aPos"), uv: loc("aUV"), n: loc("aN"), mvp: uni("uMVP"), eye: uni("uEye"),
              sun: uni("uSun"), tex: uni("uTex"), haze: uni("uHaze"), fog: uni("uFog") };

  // ---- data --------------------------------------------------------------------------
  const layers = {};   // name -> {meta, z: Float32Array, x0, y0, dx, dy}
  const meshes = [];   // {tex, vbuf, ibuf, count, type}
  let eye = { x: 0, y: 0, z: 500 }, target = { x: 0, y: 1, z: 0 }, mvp = null;

  function loadLayer(name) {
    const m = T[name];
    return fetch(m.src).then(r => { if (!r.ok) throw new Error(m.src); return r.arrayBuffer(); }).then(buf => {
      const raw = new Uint16Array(buf), z = new Float32Array(raw.length);
      for (let i = 0; i < raw.length; i++) z[i] = m.zmin + raw[i] * m.scale;
      const nw = proj([m.north, m.west]), se = proj([m.south, m.east]);
      layers[name] = { m, z, x0: nw.x, y0: nw.y, dx: (se.x - nw.x) / (m.w - 1), dy: (se.y - nw.y) / (m.h - 1) };
    });
  }

  function loadImage(src) {
    return new Promise((ok, fail) => { const img = new Image(); img.onload = () => ok(img); img.onerror = fail; img.src = src; });
  }

  function texture(img) {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, img);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    if (gl2) { // mipmaps keep the far photo from shimmering; WebGL 1 can't mipmap these sizes
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      if (aniso) gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT,
                                  Math.min(8, gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT)));
    } else {
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    }
    return t;
  }

  // One mesh: the layer's samples inside a photo tile's bounds (plus one sample
  // of overlap), every `step` samples. skip(i, j) drops a quad.
  function buildMesh(Lr, tile, tex, step, zShift, skip) {
    const m = Lr.m, W = m.w, H = m.h;
    const fi = lon => (lon - m.west) / (m.east - m.west) * (W - 1);
    const fj = lat => (m.north - lat) / (m.north - m.south) * (H - 1);
    const i0 = Math.max(0, Math.floor(fi(tile.west)) - 1), i1 = Math.min(W - 1, Math.ceil(fi(tile.east)) + 1);
    const j0 = Math.max(0, Math.floor(fj(tile.north)) - 1), j1 = Math.min(H - 1, Math.ceil(fj(tile.south)) + 1);
    const is = [], js = [];
    for (let i = i0; i < i1; i += step) is.push(i);
    is.push(i1);
    for (let j = j0; j < j1; j += step) js.push(j);
    js.push(j1);
    const nx = is.length, ny = js.length;
    const v = new Float32Array(nx * ny * 8);
    const zAt = (i, j) => Lr.z[Math.min(H - 1, Math.max(0, j)) * W + Math.min(W - 1, Math.max(0, i))];
    let o = 0;
    for (const j of js) {
      const lat = m.north + (m.south - m.north) * j / (H - 1);
      for (const i of is) {
        const lon = m.west + (m.east - m.west) * i / (W - 1);
        const x = Lr.x0 + i * Lr.dx, y = Lr.y0 + j * Lr.dy;
        // normal from neighbouring samples (east, north)
        const gx = (zAt(i + 1, j) - zAt(i - 1, j)) / (2 * Lr.dx);
        const gy = (zAt(i, j + 1) - zAt(i, j - 1)) / (2 * Lr.dy);
        const n = norm([-gx, -gy, 1]);
        v[o++] = x; v[o++] = y; v[o++] = zAt(i, j) + zShift;
        v[o++] = (lon - tile.west) / (tile.east - tile.west);
        v[o++] = (tile.north - lat) / (tile.north - tile.south);
        v[o++] = n[0]; v[o++] = n[1]; v[o++] = n[2];
      }
    }
    const idx = [];
    for (let b = 0; b < ny - 1; b++) {
      for (let a = 0; a < nx - 1; a++) {
        if (skip && skip(is[a], js[b], is[a + 1], js[b + 1])) continue;
        const k = b * nx + a;
        idx.push(k, k + nx, k + 1, k + 1, k + nx, k + nx + 1);
      }
    }
    const big = nx * ny > 65535;
    if (big && !uint32) return buildMesh(Lr, tile, tex, step * 2, zShift, skip); // too many vertices for 16-bit indices
    const vbuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vbuf);
    gl.bufferData(gl.ARRAY_BUFFER, v, gl.STATIC_DRAW);
    const ibuf = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibuf);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, big ? new Uint32Array(idx) : new Uint16Array(idx), gl.STATIC_DRAW);
    meshes.push({ tex, vbuf, ibuf, count: idx.length, type: big ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT });
  }

  const ready = Promise.all([loadLayer("detail"), loadLayer("wide"),
                             Promise.all(A.tiles.map(t => loadImage(t.src).then(img => ({ t, img }))))])
    .then(([, , imgs]) => {
      const D = T.detail, step = opts.step || 1;
      for (const { t, img } of imgs) {
        if (t.src.includes("wide")) {
          // the wide layer goes under the course photo: drop its quads that the
          // detail mesh covers, and sink it a little where they overlap
          const Wm = T.wide, lon = i => Wm.west + (Wm.east - Wm.west) * i / (Wm.w - 1),
                lat = j => Wm.north + (Wm.south - Wm.north) * j / (Wm.h - 1);
          const inside = (i, j) => lon(i) > D.west && lon(i) < D.east && lat(j) < D.north && lat(j) > D.south;
          buildMesh(layers.wide, t, texture(img), 1, -1.5,
                    (ia, ja, ib, jb) => inside(ia, ja) && inside(ib, jb) && inside(ia, jb) && inside(ib, ja));
        } else {
          buildMesh(layers.detail, t, texture(img), step, 0, null);
        }
      }
      resize();
    });

  // ---- view ----------------------------------------------------------------------
  function heightAt(p) {
    for (const name of ["detail", "wide"]) {
      const Lr = layers[name];
      if (!Lr) continue;
      const fi = (p.x - Lr.x0) / Lr.dx, fj = (p.y - Lr.y0) / Lr.dy, W = Lr.m.w, H = Lr.m.h;
      if (fi < 0 || fj < 0 || fi > W - 1 || fj > H - 1) continue;
      const i = Math.min(Math.floor(fi), W - 2), j = Math.min(Math.floor(fj), H - 2), a = fi - i, b = fj - j;
      const z = Lr.z;
      return (z[j * W + i] * (1 - a) + z[j * W + i + 1] * a) * (1 - b) +
             (z[(j + 1) * W + i] * (1 - a) + z[(j + 1) * W + i + 1] * a) * b;
    }
    return T.detail.zmin;
  }

  function setView(e, t) {
    eye = { ...e, z: Math.max(e.z, heightAt(e) + 2) }; // never under the ground
    target = t;
    const aspect = canvas.width / canvas.height;
    const fovY = 2 * Math.atan(Math.tan(FOV_H / 2) / aspect);
    const fwd = norm([target.x - eye.x, target.y - eye.y, target.z - eye.z]);
    const up = Math.abs(fwd[2]) > 0.999 ? [0, 1, 0] : [0, 0, 1];
    mvp = mul(perspective(fovY, aspect, 2, 8000), lookAt([eye.x, eye.y, eye.z], [target.x, target.y, target.z], up));
  }

  function render() {
    if (!mvp) return;
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(HAZE[0], HAZE[1], HAZE[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.useProgram(prog);
    gl.uniformMatrix4fv(L.mvp, false, mvp);
    gl.uniform3f(L.eye, eye.x, eye.y, eye.z);
    gl.uniform3fv(L.sun, SUN);
    gl.uniform3fv(L.haze, HAZE);
    gl.uniform2f(L.fog, 900, 3200);
    gl.uniform1i(L.tex, 0);
    gl.activeTexture(gl.TEXTURE0);
    for (const m of meshes) {
      gl.bindTexture(gl.TEXTURE_2D, m.tex);
      gl.bindBuffer(gl.ARRAY_BUFFER, m.vbuf);
      gl.vertexAttribPointer(L.pos, 3, gl.FLOAT, false, 32, 0);
      gl.vertexAttribPointer(L.uv, 2, gl.FLOAT, false, 32, 12);
      gl.vertexAttribPointer(L.n, 3, gl.FLOAT, false, 32, 20);
      gl.enableVertexAttribArray(L.pos);
      gl.enableVertexAttribArray(L.uv);
      gl.enableVertexAttribArray(L.n);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.ibuf);
      gl.drawElements(gl.TRIANGLES, m.count, m.type, 0);
    }
  }

  function project(p) {
    if (!mvp) return null;
    const z = p.z == null ? heightAt(p) : p.z, M = mvp;
    const cx = M[0] * p.x + M[4] * p.y + M[8] * z + M[12];
    const cy = M[1] * p.x + M[5] * p.y + M[9] * z + M[13];
    const cw = M[3] * p.x + M[7] * p.y + M[11] * z + M[15];
    if (cw <= 0.5) return null;
    return { x: (cx / cw + 1) / 2 * canvas.width, y: (1 - cy / cw) / 2 * canvas.height, d: cw };
  }

  function resize() {
    if (eye && target && mvp) setView(eye, target);
  }

  // ---- small matrix helpers (column-major, like WebGL) ---------------------------------
  function norm(v) { const n = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / n, v[1] / n, v[2] / n]; }
  function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
  function perspective(fovY, aspect, near, far) {
    const f = 1 / Math.tan(fovY / 2), nf = 1 / (near - far);
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
  }
  function lookAt(e, c, up) {
    const z = norm([e[0] - c[0], e[1] - c[1], e[2] - c[2]]), x = norm(cross(up, z)), y = cross(z, x);
    return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
      -(x[0] * e[0] + x[1] * e[1] + x[2] * e[2]), -(y[0] * e[0] + y[1] * e[1] + y[2] * e[2]),
      -(z[0] * e[0] + z[1] * e[1] + z[2] * e[2]), 1]);
  }
  function mul(a, b) {
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
    return o;
  }

  return { ready, setView, render, project, heightAt, resize, webgl2: gl2 };
};
