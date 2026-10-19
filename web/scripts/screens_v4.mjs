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
const OUT = resolve(args.out || join(HERE, "..", "..", "untracked", "screens", "web-v4"));
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
    const api = async (p) => (await page.request.get(`${base}api/gateway/${p}`)).json();
    const insights = (await api("note/insights?acked=false&limit=50")).items;
    const review = (await api("note/review?limit=50")).items;
    const overview = await api("note/overview?days=30");
    const want = {
      thoughts: insights.length + review.filter((r) => r.kind === "contradiction").length + (overview.resurfacing || []).filter((n) => !insights.some((i) => i.note_id === n.note_id)).length,
      loops: (await api("note/tasks?status=open&limit=50")).items.length,
      review: review.filter((r) => r.kind !== "contradiction").length,
      themes: (await api("note/communities")).items.length,
    };
    const PANELS = ["changed", "ask", "thoughts", "thennow", "loops", "review", "themes"];
    const noScroll = async (label) => {
      const m = await page.evaluate(() => {
        const d = document.scrollingElement;
        const main = document.querySelector(".nt-main");
        return { doc: [d.scrollHeight, d.clientHeight], docW: [d.scrollWidth, d.clientWidth], main: [main.scrollHeight, main.clientHeight] };
      });
      check(m.doc[0] <= m.doc[1] && m.main[0] <= m.main[1] && m.docW[0] <= m.docW[1], `${label}: no page scroll (document ${m.doc.join("/")}, main ${m.main.join("/")}, width ${m.docW.join("/")})`);
    };

    for (const theme of THEMES) {
      await page.evaluate((t) => localStorage.setItem("af_appearance_abstractnote_v1", JSON.stringify({ theme: t, font_scale: "md", header_density: "standard" })), theme);
      for (const width of WIDTHS) {
        await page.setViewportSize({ width, height: 800 });
        await page.goto(`${base}#/remember`);
        await page.reload();
        await page.locator(".nt-rem__tab").first().waitFor();
        await page.locator('.nt-rem__tab[aria-selected="true"]', { hasText: "What changed" }).waitFor();
        await page.waitForTimeout(400);
        if (theme === THEMES[0]) {
          for (const [id, n] of Object.entries(want)) {
            const got = await page.locator(`#rem-tab-${id} .nt-rem__badge`).getAttribute("data-count").catch(() => null);
            check(Number(got) === n, `${width}: the ${id} badge counts ${n} (shows ${got})`);
          }
        }
        for (const id of PANELS) {
          await page.locator(`#rem-tab-${id}`).click();
          await page.locator(`.nt-rem__panel[data-panel="${id}"]`).waitFor();
          check((await page.locator(`#rem-tab-${id}`).getAttribute("aria-selected")) === "true", `${theme} ${width}: clicking ${id} shows it`);
          if (id === "ask") {
            await page.getByRole("searchbox", { name: "Question" }).fill("LUT budget");
            await page.getByRole("button", { name: "Ask", exact: true }).click();
            await page.locator(".nt-answer .nt-answer-text").waitFor({ timeout: 30_000 });
          }
          if (id === "thoughts") await page.locator(".nt-thought").first().waitFor();
          if (id === "thennow") await page.locator(".nt-thennow").first().waitFor();
          if (id === "loops") await page.locator(".nt-row--loop").first().waitFor();
          if (id === "review") await page.locator(".nt-review .nt-item").first().waitFor();
          if (id === "themes") await page.locator(".nt-themecard").first().waitFor();
          if (id === "changed") await page.locator(".nt-row").first().waitFor();
          await page.waitForTimeout(250);
          await noScroll(`${theme} ${width} ${id}`);
          await page.screenshot({ path: join(OUT, `remember-${id}-${theme}-${width}.png`) });
        }
        if (theme === THEMES[0]) {
          // Keys: 1–7 choose a panel, "/" goes to the question, typing there opens Ask.
          await page.locator(".nt-rem__panel").click({ position: { x: 5, y: 5 } });
          for (let i = 0; i < PANELS.length; i++) {
            await page.keyboard.press(String(i + 1));
            await page.waitForTimeout(80);
            check((await page.locator(".nt-rem__panel").getAttribute("data-panel")) === PANELS[i], `${width}: key ${i + 1} opens ${PANELS[i]}`);
          }
          await page.keyboard.press("3");
          await page.keyboard.press("/");
          check(await page.getByRole("searchbox", { name: "Question" }).evaluate((el) => el === document.activeElement), `${width}: "/" goes to the question`);
          await page.keyboard.type("2");
          await page.waitForTimeout(100);
          check((await page.locator(".nt-rem__panel").getAttribute("data-panel")) === "ask", `${width}: typing a question opens Ask, and digits typed there stay in the question`);
          // Worth a thought: compact cards, at most two actions, bodies at most two lines until More.
          await page.goto(`${base}#/remember?panel=thoughts`);
          await page.locator(".nt-thought").first().waitFor();
          const acts = await page.locator(".nt-thought__actions").evaluateAll((els) => Math.max(...els.map((e) => e.querySelectorAll("button").length)));
          check(acts <= 2, `${width}: each card has at most two actions (${acts})`);
          const tall = await page.locator(".nt-thought__body").evaluateAll((els) => els.filter((e) => e.getBoundingClientRect().height > 2 * parseFloat(getComputedStyle(e).lineHeight) + 2).length);
          check(tall === 0, `${width}: card bodies show two lines at most until More (${tall} taller)`);
          const groups = await page.locator(".nt-group__head").allInnerTexts();
          check(groups.some((g) => g.startsWith("Tensions")) && groups.some((g) => g.startsWith("Questions")) && groups.some((g) => g.startsWith("Reminders")), `${width}: grouped by kind (${groups.map((g) => g.split("\n")[0]).join(", ")})`);
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
