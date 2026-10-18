#!/usr/bin/env node
// The build must work at / and under /apps/note/: every asset URL in the built
// index.html is relative (no leading "/"), per the app-server mount contract rule 3.
import { readFileSync } from "node:fs";
import { join } from "node:path";

const dist = process.argv[2] || "dist";
const html = readFileSync(join(dist, "index.html"), "utf8");
const bad = [...html.matchAll(/\b(?:src|href)="(\/[^"]*)"/g)].map((m) => m[1]);
if (bad.length) {
  console.error(`Absolute URLs in ${dist}/index.html (they break under /apps/note/): ${bad.join(", ")}`);
  process.exit(1);
}
console.log(`${dist}/index.html: every asset URL is relative.`);
