// HyperFrames home screen: a local page listing the user's projects. Clicking one starts that project's Studio
// server and the page moves on to it, so the Studio window opens without a file dialog.
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
const ROOTS = [
  join(desktop, "video-pipeline", "videos"),
  join(desktop, "ClaudeTransfer", "videos"),
  join(homedir(), "Desktop", "video-pipeline", "videos"),
  "F:\\video-pipeline\\videos",
];
const servers = new Set(); // PIDs of Studio servers this home screen started

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
  for (const root of ROOTS) {
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

// The Windows Explorer "Open" window, for a project that isn't in the list.
function browse() {
  const script = `Add-Type -AssemblyName System.Windows.Forms
$o = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true; ShowInTaskbar = $false; Opacity = 0 }; $o.Show()
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = 'Open a HyperFrames project (choose its index.html)'
$d.Filter = 'HyperFrames project (index.html, hyperframes.json)|index.html;hyperframes.json'
if ($d.ShowDialog($o) -eq 'OK') { Split-Path -Parent $d.FileName }`;
  const out = spawnSync("powershell.exe", ["-NoProfile", "-STA", "-Command", script], {
    encoding: "utf8",
  });
  return out.stdout.trim();
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
    if (req.method === "POST" && req.url === "/open") {
      const { dir } = await readBody(req);
      if (!dir || !isProject(dir))
        return send(res, 400, {
          error:
            "That folder isn't a HyperFrames project (it needs index.html and hyperframes.json).",
        });
      return send(res, 200, { url: startStudio(dir) });
    }
    if (req.method === "POST" && req.url === "/browse") {
      const dir = browse();
      if (!dir) return send(res, 200, { cancelled: true });
      if (!isProject(dir))
        return send(res, 400, {
          error:
            "That folder isn't a HyperFrames project (it needs index.html and hyperframes.json).",
        });
      return send(res, 200, { url: startStudio(dir) });
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
