import { describe, expect, it, vi } from "vitest";
import { ApiError, MAX_UPLOAD_BYTES, createApi, errorSentence, readCookie, retryable } from "../src/lib/api";

function respond(status: number, body: unknown = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
function scripted(...answers: Array<number | Error>) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    const a = answers.shift();
    if (a instanceof Error) throw a;
    return respond(a ?? 200, a === 200 || a === undefined ? { ok: true } : { detail: "nope", reason_code: a === 409 ? "automation_busy" : undefined });
  });
  return { fetch: fetch as unknown as typeof globalThis.fetch, calls };
}
const noSleep = vi.fn(async () => undefined);

describe("api client (ADR-0039 retry rule)", () => {
  it("retries a read on 5xx and network errors, 3 tries in all, with 1 s then 3 s back-off", async () => {
    const s = scripted(503, new TypeError("offline"), 200);
    const sleep = vi.fn(async () => undefined);
    const api = createApi({ fetch: s.fetch, sleep, csrf: () => "c" });
    await expect(api.get("note/cues")).resolves.toEqual({ ok: true });
    expect(s.calls).toHaveLength(3);
    expect(sleep.mock.calls.map((c) => (c as unknown[])[0])).toEqual([1000, 3000]);
  });

  it("reports the error once every try failed", async () => {
    const s = scripted(503, 502, 500, 200);
    const api = createApi({ fetch: s.fetch, sleep: noSleep });
    await expect(api.get("note/cues")).rejects.toMatchObject({ status: 500 });
    expect(s.calls).toHaveLength(3);
  });

  it("never retries a refusal (4xx) and calls onUnauthorized on 401", async () => {
    const s = scripted(404);
    const api = createApi({ fetch: s.fetch, sleep: noSleep });
    await expect(api.get("note/notes/x")).rejects.toMatchObject({ status: 404 });
    expect(s.calls).toHaveLength(1);
    const s2 = scripted(401);
    const onUnauthorized = vi.fn();
    await expect(createApi({ fetch: s2.fetch, sleep: noSleep, onUnauthorized }).get("note/cues")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("sends a write once, with the app's CSRF header, even on 503", async () => {
    const s = scripted(503, 200);
    const api = createApi({ fetch: s.fetch, sleep: noSleep, csrf: () => "tok" });
    await expect(api.post("note/insights/a/ack")).rejects.toMatchObject({ status: 503 });
    expect(s.calls).toHaveLength(1);
    expect((s.calls[0].init?.headers as Record<string, string>)["x-abstractnote-csrf"]).toBe("tok");
    expect(s.calls[0].url).toBe("api/gateway/note/insights/a/ack");
  });

  it("uses relative URLs and drops empty query values", async () => {
    const s = scripted(200);
    await createApi({ fetch: s.fetch }).get("note/graph", { focus: ["a", "b"], depth: 2, at: null, kinds: [] });
    expect(s.calls[0].url).toBe("api/gateway/note/graph?focus=a%2Cb&depth=2");
    expect(s.calls[0].url.startsWith("/")).toBe(false);
  });

  it("refuses a file over 25 MB before uploading it", async () => {
    const s = scripted(200);
    const big = { size: MAX_UPLOAD_BYTES + 1, name: "film.mov", type: "video/quicktime" } as File;
    await expect(createApi({ fetch: s.fetch }).upload(big, "note-web-x")).rejects.toMatchObject({ code: "too_large" });
    expect(s.calls).toHaveLength(0);
    expect(errorSentence(new ApiError(413, "", "too_large"))).toContain("abstractnote import files");
  });

  it("says 'Already running' for 409 automation_busy and never shows a status code", async () => {
    expect(errorSentence(new ApiError(409, "busy", "automation_busy"))).toBe("Already running.");
    for (const st of [0, 401, 403, 404, 500, 418]) expect(errorSentence(new ApiError(st, "x"), "the queue")).not.toMatch(/\d{3}|HTTP/);
  });

  it("reads the automations error shape {detail: {reason_code, message}}", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ detail: { reason_code: "automation_busy", message: "An occurrence is already running." } }), { status: 409 }));
    const api = createApi({ fetch: fetch as unknown as typeof globalThis.fetch, csrf: () => "c" });
    const err = await api.post("automations/a/commands", { command_id: "x", type: "automation.run_now" }).catch((e) => e);
    expect(err).toMatchObject({ status: 409, code: "automation_busy" });
    expect(errorSentence(err)).toBe("Already running.");
  });

  it("classifies retryable statuses", () => {
    expect([0, 408, 429, 500, 503].every(retryable)).toBe(true);
    expect([400, 401, 403, 404, 409, 413, 501].some(retryable)).toBe(false);
  });

  it("reads its own CSRF cookie only", () => {
    expect(readCookie("abstractnote_gateway_csrf", "abstractflow_gateway_csrf=x; abstractnote_gateway_csrf=y%3D")).toBe("y=");
  });
});
