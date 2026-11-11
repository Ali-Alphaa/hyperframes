#!/usr/bin/env node
// Studio's 600-line limit for staged source files (the lefthook `filesize` check). A Node script instead of the
// inline shell loop, which lefthook mangled on Windows ("unexpected end of file from `if'").
import { readFileSync } from "node:fs";

const MAX_LINES = 600;
const skip = /\.test\.(ts|tsx)$|\.generated\./;
let failed = false;

for (const file of process.argv.slice(2)) {
  if (skip.test(file)) continue;
  const text = readFileSync(file, "utf8");
  const lines = text.split("\n").length - (text.endsWith("\n") ? 1 : 0);
  if (lines > MAX_LINES) {
    console.error(`ERROR: ${file} has ${lines} lines (max ${MAX_LINES})`);
    failed = true;
  }
}

process.exit(failed ? 1 : 0);
