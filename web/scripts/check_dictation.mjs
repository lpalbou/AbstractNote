// Built Note app, virtual microphone, disposable mock Gateway (its own upload + transcribe routes, through the
// app server's session proxy). No physical recording or note writes. Three gateways:
//   - with speech-to-text: one shared mic, the recording uploaded under voice/dictation/, the transcript appended
//     to the draft, no automatic save, and starting a recording keeps Capture's other notice;
//   - advertising speech-to-text whose transcription fails: one plain sentence, never the gateway's detail;
//   - without speech-to-text: the mic is disabled with a plain reason and nothing is uploaded.
// `node scripts/check_dictation.mjs [screenshot dir]` (after `npm run build`).
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
const out = process.argv[2] ? resolve(process.argv[2]) : null;
if (out) mkdirSync(out, { recursive: true });
const MISSING = "Your gateway has no speech-to-text set up, so dictation is unavailable.";
const FAILED = "Your gateway could not transcribe the recording. Try again.";
const TOO_LARGE = /larger than 25 MB/;
let browser;

async function withGateway(options, viewport, run) {
  const gateway = createMockGateway(options);
  const mock = createServer(gateway.handler);
  const app = createNoteServer({ distDir: fileURLToPath(new URL("../dist", import.meta.url)), gatewayUrl: await listen(mock) });
  const url = await listen(app);
  const page = await browser.newPage({ viewport, permissions: ["microphone"] });
  const errors = [], writes = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("request", r => { if (r.method() === "POST" && (r.url().includes("/note/") || r.url().includes("/runs/start"))) writes.push(r.url()); });
  try {
    await page.goto(url);
    await page.locator("#gateway-session-user").fill("demo");
    await page.locator("#gateway-session-token").fill(MOCK_TOKEN);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    const draft = page.getByRole("textbox", { name: "Note", exact: true });
    await draft.fill("Existing draft.");
    const mic = page.getByRole("button", { name: "Hold to dictate a note", exact: true });
    await mic.waitFor();
    assert.equal(await page.locator('.nt-composer [data-action="voice-mic"]').count(), 1);
    await run({ page, mic, state: gateway.state });
    assert.deepEqual(writes, []); assert.deepEqual(errors, []);
  } finally {
    await page.close(); app.close(); mock.close(); gateway.stop?.();
  }
}

async function dictate(page, mic) {
  await mic.hover(); await page.mouse.down();
  await page.getByRole("button", { name: "Recording — release to send", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Save", exact: true }).isEnabled(), false);
  await page.waitForTimeout(900); await page.mouse.up();
}
const draftValue = page => page.locator('textarea[aria-label="Note"]').inputValue();

try {
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined, args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", `--use-file-for-fake-audio-capture=${microphone}`, "--mute-audio"] });

  await withGateway({}, { width: 1280, height: 900 }, async ({ page, mic, state }) => {
    assert.equal(await mic.isEnabled(), true);
    // A notice Capture already shows (a file over 25 MB) survives the recording that follows.
    await page.getByRole("main").locator('input[type="file"]').setInputFiles({ name: "big.bin", mimeType: "application/octet-stream", buffer: Buffer.alloc(26 * 1024 * 1024) });
    await page.getByText(TOO_LARGE).waitFor();
    await dictate(page, mic);
    await page.waitForFunction(() => document.querySelector('textarea[aria-label="Note"]')?.value === "Existing draft.\nRemember this spoken idea.");
    assert.equal(await page.getByText(TOO_LARGE).count(), 1, "starting dictation cleared Capture's other notice");
    const uploads = [...state.artifacts.values()];
    assert.equal(uploads.length, 1); assert.match(uploads[0].source_path || "", /^voice\/dictation\/.+\.(webm|ogg|m4a|wav)$/);
    assert.equal(state.transcriptions.length, 1);
    assert.equal(state.transcriptions[0].audio_artifact.$artifact, uploads[0].artifact_id);
    if (out) await page.screenshot({ path: resolve(out, "note-dictation.png") });
  });

  await withGateway({ sttFails: true }, { width: 1280, height: 900 }, async ({ page, mic, state }) => {
    await dictate(page, mic);
    const notice = page.locator("[data-dictation-notice]");
    await notice.waitFor();
    assert.equal(await notice.innerText(), FAILED);
    assert.doesNotMatch(await page.locator("body").innerText(), /STT|child run|backend/);
    assert.equal(await draftValue(page), "Existing draft.");
    assert.equal(state.transcriptions.length, 1);
  });

  for (const viewport of [{ width: 1440, height: 900 }, { width: 400, height: 860 }]) {
    await withGateway({ stt: false }, viewport, async ({ page, mic, state }) => {
      assert.equal(await mic.isDisabled(), true);
      assert.equal(await page.locator('.nt-composer [data-voice-status]').innerText(), MISSING);
      await mic.hover({ force: true }); await page.mouse.down(); await page.waitForTimeout(600); await page.mouse.up();
      assert.equal(state.artifacts.size, 0); assert.equal(state.transcriptions.length, 0);
      assert.equal(await draftValue(page), "Existing draft.");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert.ok(overflow <= 0, `horizontal overflow ${overflow}px at ${viewport.width}px`);
      if (out) await page.screenshot({ path: resolve(out, `note-dictation-unavailable-${viewport.width}.png`) });
    });
  }
  console.log("check_dictation: with speech-to-text one shared mic, recording uploaded, transcript appended, other notice kept, no automatic save; a failed transcription reads plainly; without speech-to-text the mic is disabled with its reason (1440 and 400 px)");
} finally {
  await browser?.close(); rmSync(temp, { recursive: true, force: true });
}
