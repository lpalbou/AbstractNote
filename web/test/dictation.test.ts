import { describe, expect, it, vi } from "vitest";
import { ApiError, TRANSCRIBE_TIMEOUT_MS, createApi } from "../src/lib/api";
import { DICTATION_TEXT, dictationErrorSentence, dictationUnavailableReason, sttAvailable } from "../src/lib/dictation";

const voice = (stt: unknown) => ({ capabilities: { contracts: { version: 1, assistant: { voice: { tts: { available: true }, stt } } } } });

describe("dictation is offered only when the gateway transcribes", () => {
  it("reads stt.available from the scoped answer and from an older gateway's full list", () => {
    expect(sttAvailable(voice({ available: true }))).toBe(true);
    // A gateway before 0.20.2 ignores ?scope=voice and answers everything: the same contract path.
    expect(sttAvailable({ capabilities: { abstractvoice: { installed: true }, contracts: voice({ available: true }).capabilities.contracts } })).toBe(true);
  });

  it("treats anything but available: true as no speech-to-text", () => {
    for (const payload of [voice({ available: false, reason: "No STT backend is configured." }), voice(undefined), voice({ available: "yes" }), {}, null, "x", { capabilities: {} }]) {
      expect(sttAvailable(payload)).toBe(false);
    }
  });

  it("disables the microphone with a plain reason, http first", () => {
    expect(dictationUnavailableReason("available", null)).toBeNull();
    expect(dictationUnavailableReason("checking", null)).toBeNull();
    expect(dictationUnavailableReason("missing", null)).toBe(DICTATION_TEXT.missing);
    expect(dictationUnavailableReason("unknown", null)).toBe(DICTATION_TEXT.unknown);
    const http = "This page is loaded over http, so the microphone is unavailable — open it over https.";
    for (const c of ["available", "missing", "unknown", "checking"] as const) expect(dictationUnavailableReason(c, http)).toBe(http);
    for (const text of Object.values(DICTATION_TEXT)) expect(text).not.toMatch(/\bSTT\b|child run/i);
  });

  it("asks the gateway for its voice contract only", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(voice({ available: true })), { status: 200 }));
    expect(sttAvailable(await createApi({ fetch }).voiceCapabilities())).toBe(true);
    expect(fetch.mock.calls[0][0]).toBe("api/gateway/discovery/capabilities?scope=voice");
  });
});

describe("a failed transcription reads as one plain sentence", () => {
  it("never shows the gateway's internal detail", () => {
    const detail = "STT child run did not complete successfully: No STT backend is configured.";
    const cases = [new ApiError(500, detail), new ApiError(502, detail, "unreadable"), new ApiError(0, "No answer.", "network"),
      new ApiError(0, "No answer.", "timeout"), new ApiError(413, detail), new ApiError(404, detail), new ApiError(403, detail), new Error(detail), detail];
    for (const e of cases) {
      const s = dictationErrorSentence(e);
      expect(s).not.toMatch(/STT|child run|backend|HTTP/i);
      expect(s).toMatch(/\.$/);
    }
    expect(dictationErrorSentence(new ApiError(500, detail))).toBe("Your gateway could not transcribe the recording. Try again.");
  });

  it("says a slow transcription is still running, not that there was no answer", () => {
    expect(TRANSCRIBE_TIMEOUT_MS).toBe(120_000); // the sentence names 2 minutes
    expect(dictationErrorSentence(new ApiError(0, "No answer.", "timeout"))).toMatch(/still transcribing after 2 minutes.*Try again/);
    expect(dictationErrorSentence(new ApiError(0, "No answer.", "network"))).toMatch(/could not be reached/);
  });

  it("tells a timeout from a lost connection", async () => {
    const hang = vi.fn((_: RequestInfo | URL, init: RequestInit = {}) => new Promise<Response>((_, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    await expect(createApi({ fetch: hang as typeof fetch, timeoutMs: 5, backoffMs: [] }).get("x")).rejects.toMatchObject({ status: 0, code: "timeout" });
    const down = vi.fn(async () => { throw new TypeError("Failed to fetch"); });
    await expect(createApi({ fetch: down, backoffMs: [] }).get("x")).rejects.toMatchObject({ status: 0, code: "network" });
  });
});
