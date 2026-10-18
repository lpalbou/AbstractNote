#!/usr/bin/env node
/**
 * Playwright smoke + screenshots against the mock gateway (default) or a real
 * gateway (`--gateway-url URL --user U --token T`):
 *   - starts the production server (bin/server.js) in-process on 127.0.0.1;
 *   - signs in through the app's own connection endpoint (the session proxy);
 *   - Capture: sends a note, checks the early answer from the ledger stream
 *     equals GET /runs/{id} output.response (review 01 check S3), and waits
 *     until the note is processed and its links appear in the thread (M8);
 *   - screenshots Capture, Graph and Remember in three themes at 1280 and 400 px.
 *
 *   PLAYWRIGHT_BROWSERS_PATH=<scratch> node scripts/screens.mjs [--out DIR] [--themes a,b] [--widths 1280,400]
 *       [--gateway-url URL --user U --token T] [--only-smoke]
 */

import * as http from "node:http";
import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

import { createNoteServer } from "../bin/server.js";
import { createMockGateway, MOCK_TOKEN } from "../mock/server.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => {
    if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : "1"]);
    return acc;
  }, []),
);
const OUT = resolve(args.out || join(HERE, "..", "..", "untracked", "screens", "web"));
const THEMES = String(args.themes || "dark,observer-night,solarized-light").split(",");
const WIDTHS = String(args.widths || "1280,400").split(",").map(Number);
const live = Boolean(args["gateway-url"]);

function listen(server, port) {
  return new Promise((ok, fail) => {
    server.once("error", fail);
    server.listen(port, "127.0.0.1", () => ok(server.address().port));
  });
}

const failures = [];
function check(cond, what) {
  if (cond) console.log(`ok   ${what}`);
  else {
    console.log(`FAIL ${what}`);
    failures.push(what);
  }
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  let gatewayUrl = args["gateway-url"];
  let mockServer = null;
  if (!live) {
    const mock = createMockGateway({ processDelayMs: 2500 });
    mockServer = http.createServer(mock.handler);
    const port = await listen(mockServer, Number(args["mock-port"] || 18890));
    gatewayUrl = `http://127.0.0.1:${port}`;
  }
  const app = createNoteServer({ distDir: join(HERE, "..", "dist"), gatewayUrl });
  const appPort = await listen(app, Number(args["app-port"] || 18891));
  const base = `http://127.0.0.1:${appPort}/`;
  console.log(`app ${base} -> gateway ${gatewayUrl}${live ? " (live)" : " (mock)"}`);

  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => failures.push(`page error: ${e.message}`));
    const signIn = await page.request.post(`${base}api/connection/gateway`, { data: { gateway_user_id: args.user || "demo", gateway_token: args.token || MOCK_TOKEN, persist: false } });
    check(signIn.ok(), "sign in through the app's connection endpoint");

    // ---- Capture smoke (S3 + M8)
    await page.goto(`${base}#/capture`);
    const text = `Met Dana about the watch timeline; she wants a beta by December. (${new Date().toISOString().slice(11, 19)})`;
    await page.getByRole("textbox", { name: "Note" }).fill(text);
    const startResp = page.waitForResponse((r) => r.url().includes("api/gateway/runs/start") && r.request().method() === "POST");
    await page.getByRole("button", { name: "Save" }).click();
    const runId = (await (await startResp).json()).run_id;
    check(typeof runId === "string" && runId.length > 0, "Capture started note-capture");
    const answer = page.locator(".pc-chat-body .nt-answer").last();
    await answer.locator("p", { hasText: "Saved as" }).first().waitFor({ timeout: 60_000 });
    const filing = page.locator(".nt-filing").last();
    const source = await filing.getAttribute("data-answer-source");
    const shown = (await answer.locator(".pc-markdown, p").first().innerText()).trim();
    const run = await (await page.request.get(`${base}api/gateway/runs/${runId}`)).json();
    check(source === "stream", `the answer came from the ledger stream (got ${source})`);
    check(typeof run?.output?.response === "string" && run.output.response.trim() === shown, `"Saved as" shown equals GET /runs/{id} output.response`);
    if (!live) {
      await page.locator('.nt-filing[data-state="processed"]').last().waitFor({ timeout: 30_000 });
      check((await filing.locator(".nt-filing__row[aria-label='Linked to']").count()) === 1, "the queued note got its links in the thread once processed");
    } else {
      await page.locator('.nt-filing[data-state="queued"], .nt-filing[data-state="processed"]').last().waitFor({ timeout: 60_000 });
    }
    if (args["only-smoke"]) return;

    // ---- Screenshots
    for (const theme of THEMES) {
      await page.evaluate((t) => localStorage.setItem("af_appearance_abstractnote_v1", JSON.stringify({ theme: t, font_scale: "md", header_density: "standard" })), theme);
      for (const width of WIDTHS) {
        await page.setViewportSize({ width, height: width < 600 ? 860 : 860 });
        const shot = async (name, full = true) => page.screenshot({ path: join(OUT, `${name}-${theme}-${width}.png`), fullPage: full });
        // The app scrolls inside its main area; for a picture of the whole view, let the page grow.
        const fullShot = async (name) => {
          const tag = await page.addStyleTag({ content: ".nt-app{height:auto!important}.nt-main{overflow:visible!important}.nt-graph{height:auto!important}" });
          await page.waitForTimeout(150);
          await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: true });
          await tag.evaluate((el) => el.remove());
        };
        await page.goto(`${base}#/capture`);
        await page.reload();
        await page.locator(".nt-filing").last().waitFor();
        await shot("capture", false);
        await page.goto(`${base}#/graph?focus=ex:project-lut&depth=2&entity=ex:person-dana`);
        await page.locator(".nt-node").first().waitFor();
        await page.locator(".nt-panel h2").first().waitFor();
        await page.waitForTimeout(300);
        await shot("graph", false);
        await fullShot(`graph-full-${theme}-${width}`);
        await page.goto(`${base}#/remember`);
        await page.locator(".nt-card").first().waitFor();
        await page.locator(".nt-thennow").first().waitFor();
        await page.locator(".nt-revisit .nt-item").first().waitFor();
        await page.waitForTimeout(300);
        await shot("remember", false);
        await fullShot(`remember-full-${theme}-${width}`);
      }
    }
    // One extra state each, default theme, wide: an answer with sources, a scrubbed graph, the evidence chain, settings.
    await page.evaluate(() => localStorage.setItem("af_appearance_abstractnote_v1", JSON.stringify({ theme: "dark", font_scale: "md", header_density: "standard" })));
    await page.setViewportSize({ width: 1280, height: 860 });
    await page.goto(`${base}#/remember`);
    await page.reload();
    await page.getByRole("searchbox", { name: "Question" }).fill("LUT budget");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await page.locator(".nt-answer-card .nt-cite").first().waitFor({ timeout: 30_000 });
    await page.locator(".nt-answer-card").scrollIntoViewIfNeeded();
    await page.screenshot({ path: join(OUT, "remember-answer-dark-1280.png") });
    await page.goto(`${base}#/graph?focus=ex:project-lut&depth=2&entity=ex:project-lut`);
    await page.locator(".nt-node").first().waitFor();
    const slider = page.getByRole("slider", { name: "Show the notebook as it was on" });
    await slider.evaluate((el) => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      set.call(el, "420");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.locator(".nt-node--future").first().waitFor({ timeout: 10_000 });
    await page.waitForTimeout(400);
    check((await page.locator(".nt-node--future").count()) > 0, "scrubbing back fades entities that did not exist yet");
    await page.screenshot({ path: join(OUT, "graph-then-dark-1280.png"), fullPage: true });
    await page.getByRole("button", { name: "Back to now" }).click();
    await page.locator(".nt-panel .nt-list__row button", { hasText: "Why" }).first().click();
    await page.getByRole("button", { name: "This is wrong" }).waitFor();
    await page.getByRole("button", { name: "This is wrong" }).click();
    await page.getByRole("button", { name: "Keep it" }).waitFor();
    await page.screenshot({ path: join(OUT, "why-confirm-dark-1280.png") });
    await page.getByRole("button", { name: "Keep it" }).click();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Settings" }).click();
    await page.locator(".nt-queue p").first().waitFor();
    await page.getByRole("button", { name: "Process now" }).click();
    await page.getByRole("button", { name: "Process now" }).click().catch(() => undefined);
    await page.waitForTimeout(500);
    await page.screenshot({ path: join(OUT, "settings-dark-1280.png") });
    await page.setViewportSize({ width: 400, height: 860 });
    await page.screenshot({ path: join(OUT, "settings-dark-400.png") });
    if (!live) {
      // ADR-0039: three failed reads, then one sentence with Retry in place of the content.
      await page.setViewportSize({ width: 1280, height: 860 });
      await page.keyboard.press("Escape");
      await page.locator(".af-drawer-backdrop").waitFor({ state: "detached", timeout: 5000 }).catch(() => undefined);
      await page.request.post(`${gatewayUrl}/__mock/fail`, { data: { path: "/api/gateway/note/cues", count: 3, status: 503 } });
      await page.goto(`${base}#/capture`);
      await page.goto(`${base}#/remember`);
      const err = page.locator("#cues-h").locator("xpath=ancestor::section").locator(".nt-error");
      await err.waitFor({ timeout: 20_000 });
      check((await err.innerText()).includes("Retry"), "a read that failed three times shows one sentence with Retry");
      await err.scrollIntoViewIfNeeded();
      await page.screenshot({ path: join(OUT, "remember-error-dark-1280.png") });
      await err.getByRole("button", { name: "Retry" }).click();
      await page.locator(".nt-card").first().waitFor();
      check(true, "Retry loads the content");
    }
  } finally {
    await browser.close();
    app.close();
    mockServer?.close();
  }
}

main().then(
  () => {
    if (failures.length) {
      console.log(`\n${failures.length} check(s) failed:\n- ${failures.join("\n- ")}`);
      process.exit(1);
    }
    console.log(`\nAll checks passed. Screenshots in ${OUT}`);
    process.exit(0);
  },
  (e) => {
    console.error(e);
    process.exit(1);
  },
);
