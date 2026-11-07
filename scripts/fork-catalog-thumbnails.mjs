#!/usr/bin/env node
// Fork: renders a thumbnail for every catalog item that HeyGen hosts no preview for, so Studio's
// Catalog shows a picture instead of a category tile. Skips items that already have one.
// Usage: node scripts/fork-catalog-thumbnails.mjs [--jobs 4]   (needs Bun on PATH; run after bun run build)
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const jobsArg = process.argv.indexOf("--jobs");
const jobs = jobsArg > 0 ? Math.max(1, Number(process.argv[jobsArg + 1]) || 4) : 4;

const todo = [];
for (const [dir, kind] of [
  ["blocks", "blocks"],
  ["components", "components"],
]) {
  const base = join(root, "registry", dir);
  for (const name of readdirSync(base)) {
    const itemFile = join(base, name, "registry-item.json");
    if (!existsSync(itemFile)) continue;
    const item = JSON.parse(readFileSync(itemFile, "utf8"));
    const hosted = item.preview && (item.preview.poster || item.preview.video);
    const local = join(root, "docs", "images", "catalog", kind, `${name}.jpg`);
    if (!hosted && !existsSync(local)) todo.push({ name, kind });
  }
}

console.log(`${todo.length} items need a thumbnail`);
let next = 0;
let failed = 0;
function run(cmd, args) {
  return new Promise((done) => {
    const child = spawn(cmd, args, {
      cwd: root,
      stdio: "ignore",
      shell: process.platform === "win32",
    });
    child.on("exit", done);
    child.on("error", () => done(1));
  });
}

async function makeThumbnail({ name, kind }) {
  const dir = join(root, "docs", "images", "catalog", kind);
  const png = join(dir, `${name}.png`);
  if (!existsSync(png)) {
    const code = await run("bun", [
      "scripts/generate-catalog-previews.ts",
      "--only",
      name,
      "--skip-video",
    ]);
    if (code !== 0) return code;
  }
  // Studio shows a small JPEG: the full-size PNG is about 15 times larger.
  const jpg = join(dir, `${name}.jpg`);
  return run("ffmpeg", ["-v", "error", "-y", "-i", png, "-vf", "scale=640:-2", "-q:v", "4", jpg]);
}

async function worker() {
  while (next < todo.length) {
    const item = todo[next++];
    const code = await makeThumbnail(item);
    if (code !== 0) failed++;
    console.log(`${code === 0 ? "ok  " : "FAIL"} ${item.name}`);
  }
}
await Promise.all(Array.from({ length: Math.min(jobs, todo.length) }, worker));
console.log(`done: ${todo.length - failed} made, ${failed} failed`);
process.exitCode = failed ? 1 : 0;
