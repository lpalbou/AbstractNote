// Built Note app, virtual microphone, disposable mock Gateway. No physical recording or note writes.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createNoteServer } from "../bin/server.js";
import { createMockGateway, MOCK_TOKEN } from "../mock/server.mjs";

const temp = mkdtempSync(join(tmpdir(), "note-dictation-"));
const wav = Buffer.alloc(44 + 32000 * 3);
wav.write("RIFF"); wav.writeUInt32LE(wav.length - 8, 4); wav.write("WAVEfmt ", 8);
wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32);
wav.writeUInt16LE(16, 34); wav.write("data", 36); wav.writeUInt32LE(wav.length - 44, 40);
for (let i = 0; i < 48000; i++) wav.writeInt16LE(Math.round(6000 * Math.sin(2 * Math.PI * 440 * i / 16000)), 44 + i * 2);
const microphone = join(temp, "microphone.wav"); writeFileSync(microphone, wav);
const listen = server => new Promise(resolve => server.listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${server.address().port}`)));
const mock = createServer(createMockGateway().handler);
const gatewayUrl = await listen(mock);
const app = createNoteServer({ distDir: fileURLToPath(new URL("../dist", import.meta.url)), gatewayUrl });
const url = await listen(app);
let browser;
try {
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined, args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", `--use-file-for-fake-audio-capture=${microphone}`, "--mute-audio"] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, permissions: ["microphone"] });
  const errors = [], writes = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("request", r => { if (r.method() === "POST" && r.url().includes("/note/")) writes.push(r.url()); });
  let recordings = 0;
  await page.route("**/api/gateway/attachments/upload", route => {
    recordings++;
    assert.match(route.request().postData(), /voice\/dictation\//);
    return route.fulfill({ json: { run_id: "recording-owner", artifact: { $artifact: "recording" } } });
  });
  await page.route("**/api/gateway/runs/recording-owner/audio/transcribe", async route => {
    assert.deepEqual(route.request().postDataJSON(), { audio_artifact: { $artifact: "recording" } });
    await new Promise(resolve => setTimeout(resolve, 400));
    await route.fulfill({ json: { text: "Remember this spoken idea." } });
  });
  await page.goto(url);
  await page.locator("#gateway-session-user").fill("demo");
  await page.locator("#gateway-session-token").fill(MOCK_TOKEN);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const draft = page.getByRole("textbox", { name: "Note", exact: true });
  await draft.fill("Existing draft.");
  const mic = page.getByRole("button", { name: "Hold to dictate a note", exact: true });
  await mic.waitFor();
  assert.equal(await page.locator('.nt-composer [data-action="voice-mic"]').count(), 1);
  await mic.hover(); await page.mouse.down();
  await page.getByRole("button", { name: "Recording — release to send", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Save", exact: true }).isEnabled(), false);
  await page.waitForTimeout(900); await page.mouse.up();
  await page.waitForFunction(() => document.querySelector('textarea[aria-label="Note"]')?.value === "Existing draft.\nRemember this spoken idea.");
  assert.equal(recordings, 1); assert.deepEqual(writes, []); assert.deepEqual(errors, []);
  const out = process.argv[2];
  if (out) { mkdirSync(resolve(out), { recursive: true }); await page.screenshot({ path: resolve(out, "note-dictation.png") }); }
  console.log("check_dictation: one shared mic, recorded audio uploaded, transcript appended, no automatic save");
} finally {
  await browser?.close(); app.close(); mock.close(); rmSync(temp, { recursive: true, force: true });
}
