#!/usr/bin/env node
/**
 * W16 screenshots and checks against the mock gateway: Explore's hover (fade + card), a theme shown alone,
 * the Overview, and an entity's progression line, in three themes at 1280 and 400 px.
 *
 *   PLAYWRIGHT_BROWSERS_PATH=<path> node scripts/screens_v3.mjs [--out DIR] [--themes a,b] [--widths 1280,400]
 *     [--only hover,...] [--ports MOCK,APP]
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
const OUT = resolve(args.out || join(HERE, "..", "..", "untracked", "screens", "web-v3"));
const THEMES = String(args.themes || "dark,observer-night,solarized-light").split(",");
const WIDTHS = String(args.widths || "1280,400").split(",").map(Number);
const ONLY = args.only ? String(args.only).split(",") : null;
const want = (what) => !ONLY || ONLY.includes(what);

const failures = [];
function check(cond, what) {
  console.log(`${cond ? "ok  " : "FAIL"} ${what}`);
  if (!cond) failures.push(what);
}
// --ports MOCK,APP: fixed ports (default: any free one).
const PORTS = String(args.ports || "0,0").split(",").map(Number);
const listen = (server, port) => new Promise((ok, fail) => {
  server.once("error", fail);
  server.listen(port, "127.0.0.1", () => ok(server.address().port));
});

async function main() {
  mkdirSync(OUT, { recursive: true });
  const mock = createMockGateway({ processDelayMs: 500 });
  const mockServer = http.createServer(mock.handler);
  const gatewayUrl = `http://127.0.0.1:${await listen(mockServer, PORTS[0])}`;
  const app = createNoteServer({ distDir: join(HERE, "..", "dist"), gatewayUrl });
  const base = `http://127.0.0.1:${await listen(app, PORTS[1] || 0)}/`;
  // PW_CHROMIUM: a chromium executable when the installed Playwright's own build is not downloaded.
  const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => failures.push(`page error: ${e.message}`));
    await page.goto(base);
    await page.locator("#gateway-session-url").waitFor({ timeout: 20_000 });
    await page.locator("#gateway-session-user").fill("demo");
    await page.locator("#gateway-session-token").fill(MOCK_TOKEN);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.locator(".nt-account").waitFor({ timeout: 20_000 });
    const communities = (await (await page.request.get(`${base}api/gateway/note/communities`)).json()).items;
    const lutTheme = communities.find((c) => c.label === "LUT prefill").community_id;

    for (const theme of THEMES) {
      await page.evaluate((t) => localStorage.setItem("af_appearance_abstractnote_v1", JSON.stringify({ theme: t, font_scale: "md", header_density: "standard" })), theme);
      for (const width of WIDTHS) {
        const narrow = width < 600;
        await page.setViewportSize({ width, height: narrow ? 860 : 800 });
        const name = (what) => join(OUT, `${what}-${theme}-${width}.png`);

        if (want("hover")) {
          // Everything, then rest the pointer on LUT: the rest fades, a card opens beside the pointer.
          await page.goto(`${base}#/graph?view=picture`);
          await page.reload();
          await page.locator('[data-node="ex:project-lut"]').waitFor();
          await page.waitForTimeout(400);
          const labelsAtFit = await page.locator(".nt-edge__label").count();
          const dot = await page.locator('[data-node="ex:project-lut"] .nt-node__dot').boundingBox();
          await page.locator('[data-node="ex:project-lut"]').scrollIntoViewIfNeeded();
          const dot2 = await page.locator('[data-node="ex:project-lut"] .nt-node__dot').boundingBox();
          await page.mouse.move(dot2.x + dot2.width / 2, dot2.y + dot2.height / 2);
          await page.waitForTimeout(120);
          check((await page.locator(".nt-hovercard").count()) === 0, `${theme} ${width}: no card before the pointer rests`);
          await page.locator(".nt-hovercard").waitFor({ timeout: 2000 });
          await page.waitForTimeout(250);
          const faded = await page.locator(".nt-node.nt-fade").count();
          const litLabels = await page.locator(".nt-edge--lit .nt-edge__label").count();
          const card = await page.locator(".nt-hovercard").boundingBox();
          const vp = page.viewportSize();
          check(faded > 0 && (await page.locator('[data-node="ex:project-lut"].nt-fade').count()) === 0, `${theme} ${width}: hovering LUT fades the others (${faded} faded)`);
          // On a phone the fitted edges are too short for words (the card says them).
          if (!narrow) check(litLabels > 0, `${theme} ${width}: the hovered node's links show their words (${litLabels})`);
          check(card && card.x >= 0 && card.y >= 0 && card.x + card.width <= vp.width && card.y + card.height <= vp.height, `${theme} ${width}: the card is inside the window (${Math.round(card?.x)},${Math.round(card?.y)} ${Math.round(card?.width)}x${Math.round(card?.height)})`);
          const text = await page.locator(".nt-hovercard").innerText();
          check(/LUT/.test(text) && /notes/.test(text) && /LUT prefill/.test(text) && (await page.locator(".nt-hovercard__links li").count()) >= 2, `${theme} ${width}: the card names the theme, the notes and the strongest links`);
          // The card is on top (the topmost element at its centre is inside it) and covers neither the hovered
          // node nor its neighbours.
          const onTop = await page.evaluate(() => {
            const c = document.querySelector(".nt-hovercard");
            const r = c.getBoundingClientRect();
            const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return Boolean(hit && c.contains(hit));
          });
          check(onTop, `${theme} ${width}: the card is drawn on top of the picture`);
          // What keeps it on top (gate 04 m1). A test hook (window.__ntHoverCardAt) places the card; it opens on
          // keyboard focus, the pointer parked away. (1) Over the zoom buttons, which have a z-index of their own:
          // the card's z-index must win there.
          const forceCard = async (at, layered) => {
            await page.mouse.move(2, 2);
            await page.evaluate(([a, l]) => {
              window.__ntHoverCardAt = a;
              document.querySelector(".nt-canvas").style.transform = l ? "translateZ(0)" : "";
            }, [at, layered]);
            await page.evaluate(() => document.activeElement?.blur?.());
            await page.waitForTimeout(150);
            await page.locator('[data-node="ex:project-lut"]').focus();
            await page.locator(".nt-hovercard").waitFor({ timeout: 2000 });
            await page.waitForTimeout(100);
          };
          const topmostAt = (x, y) => page.evaluate(([px, py]) => {
            const c = document.querySelector(".nt-hovercard");
            const hit = document.elementFromPoint(px, py);
            const r = c.getBoundingClientRect();
            return { inCard: Boolean(hit && c.contains(hit)), hit: hit ? `${hit.tagName.toLowerCase()}.${String(hit.className?.baseVal ?? hit.className)}` : "none", x: r.left, y: r.top };
          }, [x, y]);
          const tools = await page.locator(".nt-canvas__tools").boundingBox();
          const toolsAt = { x: tools.x + tools.width / 2, y: tools.y + tools.height / 2 };
          await forceCard({ x: tools.x - 40, y: tools.y - 4 }, false);
          const overTools = await topmostAt(toolsAt.x, toolsAt.y);
          check(overTools.inCard, `${theme} ${width}: where the card overlaps the zoom buttons it is on top (${overTools.hit})`);
          // (2) Over the hovered node while the picture is drawn as its own layer (a transform: its own stacking
          // context and the containing block of anything fixed inside it): the card, outside the picture, stays
          // where it is placed and on top.
          const canvas = await page.locator(".nt-canvas").boundingBox();
          const lutDot = await page.locator('[data-node="ex:project-lut"] .nt-node__dot').boundingBox();
          const dotAt = { x: lutDot.x + lutDot.width / 2, y: lutDot.y + lutDot.height / 2 };
          const want = { x: Math.round(dotAt.x - 10), y: Math.round(dotAt.y - 10) };
          await forceCard(want, true);
          const overNode = await topmostAt(dotAt.x, dotAt.y);
          check(Math.max(canvas.x, canvas.y) >= 16, `${theme} ${width}: the picture is offset in the window (${Math.round(canvas.x)},${Math.round(canvas.y)}), so a card trapped in it would move`);
          check(overNode.inCard && Math.abs(overNode.x - want.x) <= 1 && Math.abs(overNode.y - want.y) <= 1, `${theme} ${width}: over the node of a picture drawn as its own layer the card stays put and on top (${overNode.hit} at ${Math.round(overNode.x)},${Math.round(overNode.y)}, placed at ${want.x},${want.y})`);
          await page.evaluate(() => {
            delete window.__ntHoverCardAt;
            document.querySelector(".nt-canvas").style.transform = "";
          });
          await page.evaluate(() => document.activeElement?.blur?.());
          await page.mouse.move(dot2.x + dot2.width / 2, dot2.y + dot2.height / 2);
          await page.locator(".nt-hovercard").waitFor({ timeout: 2000 });
          await page.waitForTimeout(250);
          const covered = await page.evaluate(() => {
            const c = document.querySelector(".nt-hovercard").getBoundingClientRect();
            const hit = (b) => b.left < c.right && b.right > c.left && b.top < c.bottom && b.bottom > c.top;
            return [...document.querySelectorAll(".nt-node--lit")].filter((n) => hit(n.querySelector(".nt-node__dot").getBoundingClientRect())).map((n) => n.getAttribute("data-node"));
          });
          const nodeBox = await page.locator('[data-node="ex:project-lut"]').boundingBox();
          const apart = card.x >= nodeBox.x + nodeBox.width || card.x + card.width <= nodeBox.x || card.y >= nodeBox.y + nodeBox.height || card.y + card.height <= nodeBox.y;
          check(apart, `${theme} ${width}: the card does not overlap the hovered node`);
          check(covered.length === 0, `${theme} ${width}: the card covers none of its neighbours (${covered.join(", ") || "none"})`);
          const bg = await page.locator(".nt-hovercard").evaluate((el) => getComputedStyle(el).backgroundColor);
          check(!/rgba\(.*, 0(\.\d+)?\)$/.test(bg), `${theme} ${width}: the card's surface is opaque (${bg})`);
          await page.screenshot({ path: name("explore-hover") });
          if (theme === THEMES[0] && width === WIDTHS[0]) {
            check(labelsAtFit === 0, `edge words hidden at the fitted zoom (${labelsAtFit})`);
            const dur = await page.locator(".nt-node").first().evaluate((el) => getComputedStyle(el).transitionDuration);
            check(dur === "0.16s", `the fade animates in 160 ms (${dur})`);
            await page.emulateMedia({ reducedMotion: "reduce" });
            const still = await page.locator(".nt-node").first().evaluate((el) => getComputedStyle(el).transitionDuration);
            check(still === "0s", `with reduced motion the fade is immediate (${still})`);
            await page.emulateMedia({ reducedMotion: "no-preference" });
          }
          // A node at the right edge: the card flips to stay inside.
          await page.mouse.move(2, 2);
          await page.waitForTimeout(250);
          check((await page.locator(".nt-hovercard").count()) === 0 && (await page.locator(".nt-node.nt-fade").count()) === 0, `${theme} ${width}: leaving hides the card and restores the picture`);
          void dot;
        }
        if (want("overview")) {
          // Explore's landing: what the notebook holds.
          await page.goto(`${base}#/graph`);
          await page.reload();
          await page.locator(".nt-tile").first().waitFor();
          await page.locator(".nt-themecard").first().waitFor();
          await page.waitForTimeout(300);
          const tops = await page.locator(".nt-tile").evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)));
          const rowsOf = new Set(tops);
          check(tops.length === 5 && rowsOf.size <= (narrow ? 3 : 1), `${theme} ${width}: five stat tiles, aligned in ${rowsOf.size} row(s)`);
          const marks = await page.locator(".nt-pline__mark").count();
          check(marks > 0, `${theme} ${width}: the activity line marks the weeks that stand out (${marks})`);
          await page.screenshot({ path: name("overview") });
          const grow = await page.addStyleTag({ content: ".nt-app{height:auto!important}.nt-main{overflow:visible!important}.nt-graph{height:auto!important}.nt-graph--wide .nt-graph__main{overflow:visible!important}" });
          await page.waitForTimeout(150);
          await page.screenshot({ path: name("overview-full"), fullPage: true });
          await grow.evaluate((el) => el.remove());
          // The crosshair on a marked week: exact values, inside the chart.
          const mark = await page.locator(".nt-pline__mark circle").last().boundingBox();
          await page.locator(".nt-pline").first().scrollIntoViewIfNeeded();
          const mark2 = await page.locator(".nt-pline__mark circle").last().boundingBox();
          await page.mouse.move(mark2.x + mark2.width / 2, mark2.y + mark2.height / 2);
          await page.locator(".nt-pline__tip").waitFor();
          await page.waitForTimeout(150);
          const tip = await page.locator(".nt-pline__tip").boundingBox();
          const chart = await page.locator(".nt-pline").first().boundingBox();
          check(tip && tip.x >= chart.x - 1 && tip.x + tip.width <= chart.x + chart.width + 1, `${theme} ${width}: the tooltip stays within the chart's width`);
          check(/Week of/.test(await page.locator(".nt-pline__tip").innerText()), `${theme} ${width}: the tooltip names the week and its numbers`);
          await page.screenshot({ path: name("overview-tooltip") });
          void mark;
          await page.mouse.move(1, 1);
          if (theme === THEMES[0] && width === WIDTHS[0]) {
            await page.getByRole("button", { name: "Show the numbers" }).click();
            check((await page.locator(".nt-weeks tbody tr").count()) === 16, "the numbers behind the line, as a table (16 weeks)");
            await page.locator(".nt-themecard").first().click();
            await page.locator(".nt-theme[aria-pressed=true]").waitFor();
            check(/view=picture/.test(page.url()) && /community=/.test(page.url()), "a theme card opens the picture with that theme alone");
          }
        }
        if (want("progression")) {
          // An entity's "How it evolved": the progression line above the milestones.
          await page.goto(`${base}#/graph?focus=ex:project-lut&entity=ex:project-lut`);
          await page.reload();
          await page.locator(".nt-progress-line .nt-pline__line").waitFor();
          await page.locator(".nt-milestone").first().waitFor();
          await page.waitForTimeout(300);
          const changes = await page.locator(".nt-progress-line .nt-pline__mark--changed").count();
          check(changes === 2, `${theme} ${width}: LUT's progression marks its two replaced beliefs (${changes})`);
          const line = await page.locator(".nt-progress-line").boundingBox();
          const first = await page.locator(".nt-milestone").first().boundingBox();
          check(line && first && line.y + line.height <= first.y, `${theme} ${width}: the line sits above the milestones`);
          await page.locator(".nt-progress-line").scrollIntoViewIfNeeded();
          await page.screenshot({ path: name("entity-progression") });
          const m = await page.locator(".nt-progress-line .nt-pline__mark--changed circle").first().boundingBox();
          await page.mouse.move(m.x + m.width / 2, m.y + m.height / 2);
          await page.locator(".nt-progress-line .nt-pline__tip").waitFor();
          await page.waitForTimeout(200);
          const tipText = await page.locator(".nt-progress-line .nt-pline__tip").innerText();
          check(/→/.test(tipText), `${theme} ${width}: the tooltip on a change says what changed (${tipText.replace(/\s+/g, " ").slice(0, 70)})`);
          const tip = await page.locator(".nt-progress-line .nt-pline__tip").boundingBox();
          const vp = page.viewportSize();
          check(tip && tip.x >= 0 && tip.x + tip.width <= vp.width, `${theme} ${width}: the tooltip is inside the window`);
          await page.screenshot({ path: name("entity-progression-tooltip") });
          await page.mouse.move(1, 1);
        }
        if (want("isolate")) {
          await page.goto(`${base}#/graph?view=picture&community=${encodeURIComponent(lutTheme)}`);
          await page.reload();
          await page.locator(".nt-theme[aria-pressed=true]").waitFor();
          await page.waitForTimeout(400);
          const out = await page.locator(".nt-node.nt-out").count();
          check(out > 0 && (await page.locator('[data-node="ex:project-lut"].nt-out').count()) === 0, `${theme} ${width}: a theme alone fades the others (${out})`);
          await page.screenshot({ path: name("explore-theme") });
          if (theme === THEMES[0] && width === WIDTHS[0]) {
            await page.locator(".nt-theme[aria-pressed=true]").click();
            await page.waitForTimeout(200);
            check((await page.locator(".nt-node.nt-out").count()) === 0, "clicking the pressed theme shows everything again");
            await page.locator(".nt-theme").first().click();
            await page.waitForTimeout(200);
            check((await page.locator(".nt-theme[aria-pressed=true]").count()) === 1, "a click on a theme in the legend shows it alone");
          }
        }
      }
    }
  } finally {
    await browser.close();
    app.close();
    mockServer.close();
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
