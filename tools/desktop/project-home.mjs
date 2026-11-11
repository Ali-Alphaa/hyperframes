// fallow-ignore-file unused-file complexity
// Fork launcher's home-screen server, run directly by HyperFramesFork.ps1 (no importer).
// HyperFrames home screen: a local page with the user's projects, every feature (features.json) and settings.
// Clicking a project starts its Studio server and the page moves on to it, so Studio opens without a file dialog.
// Run by HyperFramesFork.ps1: prints {"url": ...} on its first line, stops its Studio servers on POST /shutdown.
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync, mkdirSync } from "node:fs";
import http from "node:http";
import { homedir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const cli = resolve(here, "..", "..", "packages", "cli", "dist", "cli.js");
const dataDir = join(process.env.LOCALAPPDATA ?? homedir(), "HyperFramesFork");
const recentFile = join(dataDir, "recent.json");
const desktop = join(homedir(), "OneDrive", "Desktop");
const settingsFile = join(dataDir, "settings.json");
const DEFAULT_ROOTS = [
  join(desktop, "video-pipeline", "videos"),
  join(desktop, "ClaudeTransfer", "videos"),
  join(homedir(), "Desktop", "video-pipeline", "videos"),
  "F:\\video-pipeline\\videos",
];
const servers = new Set(); // PIDs of Studio servers this home screen started

// Keys the add-ons use. Only whether each is set is ever reported, never its value.
const SERVICES = [
  { key: "GEMINI_API_KEY", name: "Gemini", use: "AutoClip highlights, ghost-editor" },
  { key: "SCRAPECREATORS_API_KEY", name: "ScrapeCreators", use: "Social research skills (paid)" },
  { key: "POSTIZ_API_KEY", name: "Postiz key", use: "Publishing drafts to X" },
  { key: "POSTIZ_API_URL", name: "Postiz address", use: "Your self-hosted Postiz" },
  {
    key: "ELEVENLABS_API_KEY",
    name: "ElevenLabs",
    use: "Paid voice isolator and voices (optional)",
  },
];

function readSettings() {
  try {
    return JSON.parse(readFileSync(settingsFile, "utf8"));
  } catch {
    return {};
  }
}

function writeSettings(settings) {
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(settingsFile, JSON.stringify(settings, null, 2));
}

const roots = () => readSettings().roots ?? DEFAULT_ROOTS;

// The user's own setting for a key: this process's environment, else the Windows user settings saved after it started.
function isSet(key) {
  if (process.env[key]) return true;
  const out = spawnSync("reg.exe", ["query", "HKCU\\Environment", "/v", key], { encoding: "utf8" });
  return out.status === 0 && /REG_\w+\s+\S/.test(out.stdout);
}

function settingsView() {
  const projects = findProjects();
  return {
    roots: roots().map((path) => ({
      path,
      exists: existsSync(path),
      projects: projects.filter((p) => p.dir.toLowerCase().startsWith(path.toLowerCase() + "\\"))
        .length,
    })),
    defaults: DEFAULT_ROOTS,
    services: SERVICES.map((s) => ({ ...s, set: isSet(s.key) })),
    recent: readRecent().length,
    fork: resolve(here, "..", ".."),
    data: dataDir,
  };
}

const isProject = (dir) =>
  existsSync(join(dir, "index.html")) && existsSync(join(dir, "hyperframes.json"));

function readRecent() {
  try {
    return JSON.parse(readFileSync(recentFile, "utf8")).filter(isProject);
  } catch {
    const last = join(dataDir, "last-project.txt");
    return existsSync(last) ? [readFileSync(last, "utf8").trim()].filter(isProject) : [];
  }
}

function remember(dir) {
  mkdirSync(dataDir, { recursive: true });
  const recent = [dir, ...readRecent().filter((d) => d.toLowerCase() !== dir.toLowerCase())].slice(
    0,
    12,
  );
  writeFileSync(recentFile, JSON.stringify(recent, null, 2));
  writeFileSync(join(dataDir, "last-project.txt"), dir);
}

function edited(dir) {
  let newest = 0;
  for (const name of ["index.html", "hyperframes.json", "compositions"]) {
    const p = join(dir, name);
    if (existsSync(p)) newest = Math.max(newest, statSync(p).mtimeMs);
  }
  return newest;
}

function findProjects() {
  const seen = new Map();
  const add = (dir) => {
    const key = dir.toLowerCase();
    if (!seen.has(key) && isProject(dir))
      seen.set(key, { dir, name: basename(dir), folder: dirname(dir), edited: edited(dir) });
  };
  readRecent().forEach(add);
  for (const root of roots()) {
    if (!existsSync(root)) continue;
    for (const entry of readdirSync(root, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const dir = join(root, entry.name);
      add(dir);
      try {
        for (const inner of readdirSync(dir, { withFileTypes: true }))
          if (inner.isDirectory()) add(join(dir, inner.name));
      } catch {}
    }
  }
  const recent = readRecent().map((d) => d.toLowerCase());
  return [...seen.values()].map((p) => ({ ...p, recent: recent.indexOf(p.dir.toLowerCase()) }));
}

function stopServer(pid) {
  spawnSync("taskkill.exe", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
  servers.delete(pid);
}

function startStudio(dir) {
  // The folder is passed explicitly: with no DIR, preview names the project after an inherited PWD.
  const out = spawnSync(
    process.execPath,
    [cli, "preview", dir, "--background", "--no-open", "--json", "--force-new"],
    {
      cwd: dir,
      encoding: "utf8",
      timeout: 120000,
    },
  );
  const text = `${out.stdout ?? ""}${out.stderr ?? ""}`;
  const json = JSON.parse(text.slice(text.indexOf("{")));
  if (!json.ok) throw new Error(text);
  for (const pid of [...servers]) stopServer(pid); // one project at a time in this window
  servers.add(json.result.pid);
  remember(dir);
  return `http://${json.result.host}:${json.result.port}/`;
}

// Folder browsing for the page's own folder browser and address bar. (A Windows Explorer dialog started from this
// background process opened hidden behind the app window, because Windows doesn't let it take the foreground.)
function drives() {
  const found = [];
  for (const letter of "CDEFGHIJKLMNOPQRSTUVWXYZ")
    if (existsSync(`${letter}:\\`)) found.push(`${letter}:\\`);
  return found;
}

function places() {
  const list = [
    { name: "Desktop", path: desktop },
    { name: "Videos folders", path: roots().find((r) => existsSync(r)) },
    { name: "Home", path: homedir() },
  ];
  return [
    ...list.filter((p) => p.path && existsSync(p.path)),
    ...drives().map((d) => ({ name: d.slice(0, 2), path: d })),
  ];
}

// Turns what the user typed into a folder: quotes and file:/// removed, ~ and %VARS% expanded, a file means its folder.
function cleanPath(input) {
  let p = String(input ?? "")
    .trim()
    .replace(/^["']|["']$/g, "");
  if (/^file:\/\//i.test(p)) p = decodeURIComponent(p.replace(/^file:\/\/\/?/i, ""));
  p = p.replace(/%([^%]+)%/g, (m, v) => process.env[v] ?? m);
  if (p === "~" || p.startsWith("~\\") || p.startsWith("~/")) p = homedir() + p.slice(1);
  if (/^[a-z]:$/i.test(p)) p += "\\";
  if (!p) return "";
  p = resolve(p);
  if (existsSync(p) && !statSync(p).isDirectory()) p = dirname(p);
  return p;
}

function listDir(input) {
  const path = cleanPath(input);
  if (!path) return { path: "", parent: null, project: false, folders: [], places: places() };
  if (!existsSync(path)) return { error: `Couldn't find ${path}`, places: places() };
  let entries;
  try {
    entries = readdirSync(path, { withFileTypes: true });
  } catch {
    return { error: `Couldn't open ${path} (no access)`, places: places() };
  }
  const folders = entries
    .filter(
      (e) =>
        e.isDirectory() &&
        !e.name.startsWith(".") &&
        !/^\$|^System Volume Information$/i.test(e.name),
    )
    .map((e) => ({
      name: e.name,
      path: join(path, e.name),
      project: isProject(join(path, e.name)),
    }))
    .sort(
      (a, b) =>
        Number(b.project) - Number(a.project) ||
        a.name.localeCompare(b.name, undefined, { numeric: true }),
    );
  const parent = dirname(path) === path ? "" : dirname(path);
  return { path, parent, project: isProject(path), folders, places: places() };
}

const send = (res, code, body, type = "application/json") => {
  res.writeHead(code, { "content-type": `${type}; charset=utf-8`, "cache-control": "no-store" });
  res.end(typeof body === "string" ? body : JSON.stringify(body));
};

const readBody = (req) =>
  new Promise((ok, fail) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => {
      try {
        ok(data ? JSON.parse(data) : {});
      } catch {
        fail(new Error("Bad request."));
      }
    });
  });

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === "GET" && req.url === "/")
      return send(res, 200, readFileSync(join(here, "project-home.html"), "utf8"), "text/html");
    if (req.method === "GET" && req.url === "/projects") return send(res, 200, findProjects());
    if (req.method === "GET" && req.url === "/features")
      return send(res, 200, readFileSync(join(here, "features.json"), "utf8"));
    if (req.method === "GET" && req.url === "/settings") return send(res, 200, settingsView());
    if (req.method === "POST" && req.url === "/settings/add-folder") {
      const dir = cleanPath((await readBody(req)).path);
      if (!dir || !existsSync(dir)) return send(res, 400, { error: "That folder doesn't exist." });
      if (!roots().some((r) => r.toLowerCase() === dir.toLowerCase()))
        writeSettings({ ...readSettings(), roots: [...roots(), dir] });
      return send(res, 200, settingsView());
    }
    if (req.method === "POST" && req.url === "/settings/remove-folder") {
      const { path } = await readBody(req);
      writeSettings({ ...readSettings(), roots: roots().filter((r) => r !== path) });
      return send(res, 200, settingsView());
    }
    if (req.method === "POST" && req.url === "/settings/reset-folders") {
      const { roots: _, ...rest } = readSettings();
      writeSettings(rest);
      return send(res, 200, settingsView());
    }
    if (req.method === "POST" && req.url === "/settings/clear-recent") {
      writeFileSync(recentFile, "[]");
      return send(res, 200, settingsView());
    }
    if (req.method === "POST" && req.url === "/reveal") {
      // Shows a folder in Explorer: only the folders this page lists.
      const { path } = await readBody(req);
      const known = [
        ...roots(),
        ...findProjects().map((p) => p.dir),
        dataDir,
        resolve(here, "..", ".."),
      ];
      if (known.includes(path) && existsSync(path)) spawnSync("explorer.exe", [path]);
      return send(res, 200, { ok: true });
    }
    if (req.method === "POST" && req.url === "/open") {
      const dir = cleanPath((await readBody(req)).dir);
      if (!dir || !isProject(dir))
        return send(res, 400, {
          error:
            "That folder isn't a HyperFrames project (it needs index.html and hyperframes.json).",
        });
      return send(res, 200, { url: startStudio(dir) });
    }
    if (req.method === "POST" && req.url === "/dir") {
      const { path } = await readBody(req);
      return send(res, 200, listDir(path));
    }
    if (req.method === "POST" && req.url === "/shutdown") {
      for (const pid of [...servers]) stopServer(pid);
      send(res, 200, { ok: true });
      server.close();
      setTimeout(() => process.exit(0), 100);
      return;
    }
    send(res, 404, { error: "not found" });
  } catch (error) {
    send(res, 500, { error: String(error.message ?? error).slice(0, 1500) });
  }
});

server.listen(0, "127.0.0.1", () => {
  console.log(
    JSON.stringify({ url: `http://127.0.0.1:${server.address().port}/`, pid: process.pid }),
  );
});
