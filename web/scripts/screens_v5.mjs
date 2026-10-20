#!/usr/bin/env node
/**
 * W21 screenshots and checks of the Processing page against the mock gateway's live simulation (a drain whose
 * note advances a step every few seconds, the pace, recent revisions, two failed notes, a consolidation in
 * progress), in three themes at 1280 and 400 px.
 *
 *   PW_CHROMIUM=<chromium> node scripts/screens_v5.mjs [--out DIR] [--themes a,b] [--widths 1280,400] [--ports MOCK,APP]
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
const OUT = resolve(args.out || join(HERE, "..", "..", "untracked", "screens", "web-v5"));
const THEMES = String(args.themes || "dark,observer-night,solarized-light").split(",");
const WIDTHS = String(args.widths || "1280,400").split(",").map(Number);
const PORTS = String(args.ports || "0,0").split(",").map(Number);

const failures = [];
function check(cond, what) {
  console.log(`${cond ? "ok  " : "FAIL"} ${what}`);
  if (!cond) failures.push(what);
}
const listen = (server, port) => new Promise((ok, fail) => {
  server.once("error", fail);
  server.listen(port, "127.0.0.1", () => ok(server.address().port));
});

async function main() {
  mkdirSync(OUT, { recursive: true });
  const mock = createMockGateway({ processDelayMs: 500, liveProcessing: true, stepMs: 2500 });
  const mockServer = http.createServer(mock.handler);
  const gatewayUrl = `http://127.0.0.1:${await listen(mockServer, PORTS[0])}`;
  const control = (body) => fetch(`${gatewayUrl}/__mock/processing`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const app = createNoteServer({ distDir: join(HERE, "..", "dist"), gatewayUrl });
  const base = `http://127.0.0.1:${await listen(app, PORTS[1] || 0)}/`;
  const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => failures.push(`page error: ${e.message}`));
    let dialogs = 0;
    page.on("dialog", (d) => {
      dialogs += 1;
      void d.dismiss();
    });
    const polls = [];
    page.on("response", (r) => {
      if (r.url().includes("/api/gateway/note/processing")) polls.push({ at: Date.now(), status: r.status(), inm: r.request().headers()["if-none-match"] || null });
    });
    await page.goto(base);
    await page.locator("#gateway-session-url").waitFor({ timeout: 20_000 });
    await page.locator("#gateway-session-user").fill("demo");
    await page.locator("#gateway-session-token").fill(MOCK_TOKEN);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.locator(".nt-account").waitFor({ timeout: 20_000 });

    const noScroll = async (label) => {
      const m = await page.evaluate(() => {
        const d = document.scrollingElement;
        const main = document.querySelector(".nt-main");
        return { doc: [d.scrollHeight, d.clientHeight], docW: [d.scrollWidth, d.clientWidth], main: [main.scrollHeight, main.clientHeight] };
      });
      check(m.doc[0] <= m.doc[1] && m.main[0] <= m.main[1] && m.docW[0] <= m.docW[1], `${label}: no page scroll (document ${m.doc.join("/")}, main ${m.main.join("/")}, width ${m.docW.join("/")})`);
    };

    // ---- polling: every 3 s with If-None-Match, nothing while hidden.
    await page.goto(`${base}#/processing`);
    await page.locator(".nt-proc__sentence").waitFor();
    await page.waitForTimeout(7_500);
    const seen = polls.filter((p) => p.at > Date.now() - 7_000);
    check(seen.length >= 2 && seen.length <= 4, `polls about every 3 s (${seen.length} in 7 s)`);
    check(seen.some((p) => p.inm), "polls send If-None-Match after the first answer");
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const before = polls.length;
    await page.waitForTimeout(6_500);
    check(polls.length === before, `no polls while hidden (${polls.length - before})`);
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await page.waitForTimeout(600);
    check(polls.length > before, "polls again at once when shown");
    check(await page.locator('.nt-tab[aria-current="page"]', { hasText: "Processing" }).count() === 1, "Processing is a top-level tab");

    for (const theme of THEMES) {
      await page.evaluate((t) => localStorage.setItem("af_appearance_abstractnote_v1", JSON.stringify({ theme: t, font_scale: "md", header_density: "standard" })), theme);
      for (const width of WIDTHS) {
        await page.setViewportSize({ width, height: 800 });
        await page.goto(`${base}#/processing`);
        await page.reload();
        await page.locator(".nt-proc__sentence").waitFor();
        await page.locator(".nt-stepper__step--current").first().waitFor({ timeout: 15_000 });
        await page.waitForTimeout(1200);
        const sentence = await page.locator(".nt-proc__sentence").innerText();
        check(/^Understanding your notes · .* per minute · about .* left$/.test(sentence), `${theme} ${width}: the state sentence (${sentence})`);
        await noScroll(`${theme} ${width}`);
        await page.screenshot({ path: join(OUT, `processing-${theme}-${width}.png`) });
        if (width >= 900) {
          for (const s of ["now", "pace", "recent", "attention"]) check(await page.locator(`[data-section="${s}"]`).isVisible(), `${theme} ${width}: ${s} is on the screen`);
          check((await page.locator(".nt-attn__row").count()) === 2, `${theme} ${width}: two failed notes`);
          check((await page.locator(".nt-feed__item").count()) >= 8, `${theme} ${width}: the feed shows the recent notes`);
          check((await page.locator(".nt-proc-panel--cons .nt-stepper__step--current").count()) === 1, `${theme} ${width}: consolidation shows its stage`);
          // Hover the pace line: a tooltip inside the window.
          const box = await page.locator(".nt-paceline svg").boundingBox();
          await page.mouse.move(box.x + box.width * 0.6, box.y + box.height / 2);
          await page.waitForTimeout(150);
          const tip = await page.locator(".nt-paceline__tip").boundingBox();
          check(Boolean(tip) && tip.x >= 0 && tip.x + tip.width <= width && tip.y >= 0, `${theme} ${width}: the pace tooltip shows inside the window`);
          await page.screenshot({ path: join(OUT, `processing-pace-hover-${theme}-${width}.png`) });
          await page.mouse.move(5, 790);
        } else {
          for (const [s, label] of [["pace", "Pace"], ["recent", "Just understood"], ["attention", "Needs attention"]]) {
            await page.getByRole("tab", { name: label }).click();
            await page.locator(`.nt-proc__one[data-section="${s}"]`).waitFor();
            await page.waitForTimeout(300);
            await noScroll(`${theme} ${width} ${s}`);
            await page.screenshot({ path: join(OUT, `processing-${s}-${theme}-${width}.png`) });
          }
          await page.getByRole("tab", { name: "Right now" }).click();
        }
      }
    }

    // ---- actions (first theme, 1280): cancel confirms inline, move to front, pause shows state, repair.
    await page.evaluate((t) => localStorage.setItem("af_appearance_abstractnote_v1", JSON.stringify({ theme: t, font_scale: "md", header_density: "standard" })), THEMES[0]);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.reload();
    await page.locator(".nt-proc-card--drain").waitFor({ timeout: 15_000 });
    await page.locator(".nt-proc-card--drain").getByRole("button", { name: "Cancel" }).click();
    await page.locator(".nt-proc-confirm").waitFor();
    await page.screenshot({ path: join(OUT, `processing-cancel-confirm-${THEMES[0]}-1280.png`) });
    check(dialogs === 0, "cancel asks inline, never with a browser dialog");
    await page.getByRole("button", { name: "Keep going" }).click();
    const firstBefore = await page.locator(".nt-waiting__title").first().innerText();
    const lastRow = page.locator(".nt-waiting__row").last();
    const lastTitle = await lastRow.locator(".nt-waiting__title").innerText();
    await lastRow.getByRole("button", { name: "Move to front" }).click();
    await page.waitForTimeout(800);
    const tops = await page.locator(".nt-waiting__title").evaluateAll((els) => els.slice(0, 3).map((e) => e.textContent));
    check(tops.includes(lastTitle), `Move to front puts "${lastTitle}" at the front (was behind "${firstBefore}")`);
    // Keyboard: the switch is reachable by Tab and pauses.
    const sw = page.getByRole("switch", { name: "Automatic understanding" });
    await sw.focus();
    await page.keyboard.press("Space");
    await page.waitForTimeout(3_800);
    const paused = await page.locator(".nt-proc__sentence").innerText();
    check(paused.startsWith("Paused"), `pausing shows in the sentence (${paused})`);
    check((await sw.getAttribute("aria-checked")) === "false", "the switch shows the state");
    await page.screenshot({ path: join(OUT, `processing-paused-${THEMES[0]}-1280.png`) });
    for (const theme of THEMES.slice(1)) {
      await page.evaluate((t) => localStorage.setItem("af_appearance_abstractnote_v1", JSON.stringify({ theme: t, font_scale: "md", header_density: "standard" })), theme);
      for (const width of WIDTHS) {
        await page.setViewportSize({ width, height: 800 });
        await page.reload();
        await page.locator(".nt-proc__sentence", { hasText: "Paused" }).waitFor();
        await page.waitForTimeout(500);
        await page.screenshot({ path: join(OUT, `processing-paused-${theme}-${width}.png`) });
      }
    }
    await page.evaluate((t) => localStorage.setItem("af_appearance_abstractnote_v1", JSON.stringify({ theme: t, font_scale: "md", header_density: "standard" })), THEMES[0]);
    await page.setViewportSize({ width: 1280, height: 800 });
    await control({ healthy: false, reason: "It is failed and will not run again." });
    await page.reload();
    await page.locator(".nt-capsule--warn").waitFor({ timeout: 10_000 });
    await page.screenshot({ path: join(OUT, `processing-repair-${THEMES[0]}-1280.png`) });
    await page.locator(".nt-capsule--warn").click();
    await page.waitForTimeout(3_500);
    check((await page.locator(".nt-capsule--warn").count()) === 0, "Repair clears the warning");
    check(failures.filter((f) => f.startsWith("page error")).length === 0, "no page errors");
  } finally {
    await browser.close();
    mock.stop();
    mockServer.close();
    app.close();
  }
  if (failures.length) {
    console.log(`\n${failures.length} check(s) failed:\n- ${failures.join("\n- ")}`);
    process.exit(1);
  }
  console.log("\nall checks passed");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
