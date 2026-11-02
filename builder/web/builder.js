// Course builder page: sign in, search OSM for a course, build it, download it.
const $ = (id) => document.getElementById(id);
let pollTimer = null;

const api = async (path, body) => {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Trolley-Client": "1"},
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  if (r.status === 401) { show(null); throw new Error("sign in first"); }
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
};

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

const show = (user, canRegister = true) => {
  $("signin").hidden = !!user;
  $("app").hidden = !user;
  $("logout").hidden = !user;
  $("registerBtn").hidden = !canRegister;
  $("who").textContent = user ? `Signed in as ${user}` : "";
  if (user) loadJobs();
};

const auth = async (path) => {
  $("authMsg").textContent = "";
  try {
    const r = await api(path, {user: $("user").value.trim().toLowerCase(), password: $("pw").value});
    if (!r.ok) { $("authMsg").textContent = r.msg; return; }
    $("pw").value = "";
    show(r.msg);
  } catch (e) { $("authMsg").textContent = e.message; }
};

$("authForm").addEventListener("submit", (e) => { e.preventDefault(); auth("/api/login"); });
$("registerBtn").addEventListener("click", () => auth("/api/register"));
$("logout").addEventListener("click", async () => { await api("/api/logout", {}); show(null); });

$("searchForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("results").replaceChildren();
  $("searchMsg").textContent = "Searching OpenStreetMap…";
  try {
    const r = await api("/api/search?q=" + encodeURIComponent($("q").value));
    if (!r.ok) { $("searchMsg").textContent = r.msg; return; }
    $("searchMsg").textContent = r.msg.length ? "" :
      "No golf course found. Add the town and state, or check the course is mapped at openstreetmap.org.";
    r.msg.forEach((h) => $("results").append(resultRow(h)));
  } catch (err) { $("searchMsg").textContent = err.message; }
});

const resultRow = (h) => {
  const li = el("li", "card");
  const info = el("div", "grow");
  info.append(el("strong", "", h.name), el("div", "small", h.place));
  if (h.in_repo.length) info.append(el("div", "small warn", `Already in the repo as: ${h.in_repo.join(", ")}`));
  const id = el("input", "id");
  id.value = h.id;
  id.title = "Course id: the folder name in courses/ and what --course takes";
  const btn = el("button", "", "Build");
  const msg = el("div", "small msg");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const r = await api("/api/build", {osm: h.osm, id: id.value.trim()});
      msg.textContent = r.ok ? "Queued, see My builds" : r.msg;
      if (r.ok) loadJobs();
    } catch (err) { msg.textContent = err.message; }
    btn.disabled = false;
  });
  const act = el("div", "act");
  act.append(el("label", "small", "id"), id, btn);
  li.append(info, act, msg);
  return li;
};

const jobRow = (j) => {
  const li = el("li", "card");
  const info = el("div", "grow");
  info.append(el("strong", "", `${j.name}`), el("span", "small", `  ${j.course}  ·  ${j.created}`));
  const status = j.status === "running" ? `building: ${j.step || "starting"}` : j.status;
  info.append(el("div", `small status ${j.status}`, j.error ? `${status}: ${j.error}` : status));
  if (j.manifest) info.append(el("div", "small", `${j.manifest.holes} holes, ${j.manifest.par ? "par " + j.manifest.par : "par not in OSM"}`));
  if (j.warnings) info.append(el("div", "small warn", `${j.warnings} warnings: check the log (a scorecard fixes missing pars)`));
  const act = el("div", "act");
  const link = (href, text, dl) => {
    const a = el("a", "btn quiet", text);
    a.href = href;
    if (dl) a.download = dl;
    else a.target = "_blank";
    return a;
  };
  act.append(link(`/api/jobs/${j.id}/log`, "Log"));
  if (j.status === "done") {
    act.append(link(`/api/jobs/${j.id}/check.png`, "Terrain check"),
                link(`/api/jobs/${j.id}/course.zip`, "Download", `${j.course}.zip`));
  }
  li.append(info, act);
  return li;
};

const loadJobs = async () => {
  clearTimeout(pollTimer);
  try {
    const jobs = await api("/api/jobs");
    $("jobs").replaceChildren(...(jobs.length ? jobs.map(jobRow) : [el("li", "small", "No builds yet.")]));
    if (jobs.some((j) => j.status === "queued" || j.status === "running")) pollTimer = setTimeout(loadJobs, 3000);
  } catch (e) { /* signed out: show() already switched pages */ }
};

api("/api/me").then((r) => show(r.user, r.register)).catch(() => show(null));
