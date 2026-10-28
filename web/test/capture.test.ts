import { describe, expect, it, vi } from "vitest";
import { createApi } from "../src/lib/api";
import { captureInput, noteBackoff, runCapture, type CaptureItem } from "../src/lib/capture";
import type { EventSourceLike } from "../src/lib/runs";

const base: CaptureItem = { id: "i1", key: "k-123", text: "Met Dana", files: [], artifacts: [], occurred_at: "2026-10-19T09:12:00Z", location: null, state: "saving" };

describe("capture input", () => {
  it("matches the note-capture contract (docs/api.md)", () => {
    const input = captureInput({ ...base, artifacts: [{ $artifact: "a1" }], location: { lat: 48.85, lon: 2.35 }, about: { kind: "cue", id: "nt_cue", title: "Priya?" } }, true, "note-web-s");
    expect(input).toMatchObject({
      text: "Met Dana",
      session_id: "note-web-s",
      occurred_at: "2026-10-19T09:12:00Z",
      source: { client: "web" },
      idempotency_key: "k-123",
      process_inline: true,
      location: { lat: 48.85, lon: 2.35 },
      context: { attachments: [{ $artifact: "a1" }], about: { kind: "cue", id: "nt_cue", title: "Priya?" } },
    });
    expect("location" in captureInput(base, false, "note-web-s")).toBe(false);
  });

  it("dates a note of files only by its files and says when the text was pasted (ADR 0008 §7)", () => {
    const files = [{ name: "minutes.pdf", size: 10, type: "application/pdf", lastModified: 1541412000000 }];
    expect(captureInput({ ...base, text: "", files }, false, "s").occurred_at).toBeNull();
    expect(captureInput({ ...base, files }, false, "s").occurred_at).toBe("2026-10-19T09:12:00Z");
    expect(captureInput({ ...base, pasted: true }, false, "s").source).toEqual({ client: "web", origin: { pasted: true } });
    expect(captureInput(base, false, "s").source).toEqual({ client: "web" });
  });

  it("backs off 2 s, 4 s, 8 s ... up to a minute", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(noteBackoff)).toEqual([2000, 4000, 8000, 15000, 30000, 60000, 60000]);
  });
});

describe("runCapture", () => {
  function harness(noteStatuses: string[]) {
    const bodies: Array<{ url: string; body: unknown }> = [];
    const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const u = String(url);
      const body = init?.body && typeof init.body === "string" ? JSON.parse(init.body) : init?.body;
      bodies.push({ url: u, body });
      if (u.endsWith("attachments/upload")) return new Response(JSON.stringify({ artifact: { $artifact: "art-1" } }));
      if (u.endsWith("runs/start")) return new Response(JSON.stringify({ run_id: "r1" }));
      if (u.endsWith("runs/r1")) return new Response(JSON.stringify({ status: "completed", output: { response: "Saved as idea \"Met Dana\".", note_id: "nt_x", status: "queued" } }));
      if (u.includes("note/notes/nt_x")) {
        const status = noteStatuses.length > 1 ? noteStatuses.shift() : noteStatuses[0];
        return new Response(JSON.stringify({ note_id: "nt_x", title: "Met Dana", kind: "event", occurred_at: base.occurred_at, status, entities: status === "processed" ? [{ entity_id: "ex:person-dana", name: "Dana", kind: "person" }] : [], revisits: [] }));
      }
      // A gateway before the replay view (0.17.0): FastAPI's unmatched-route 404.
      return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
    });
    const api = createApi({ fetch: fetch as unknown as typeof globalThis.fetch, sleep: async () => undefined, csrf: () => "c" });
    const states: Array<Partial<CaptureItem>> = [];
    const stream = (): EventSourceLike => {
      const ls: Record<string, Array<(e: MessageEvent) => void>> = {};
      setTimeout(() => {
        for (const fn of ls.step || []) fn({ data: JSON.stringify({ cursor: 1, record: { effect: { type: "answer_user", payload: { message: "Saved as idea \"Met Dana\"." } }, status: "completed", result: { message: "Saved as idea \"Met Dana\"." } } }) } as MessageEvent);
        for (const fn of ls.done || []) fn({ data: "{}" } as MessageEvent);
      }, 0);
      return { onerror: null, addEventListener: (t, fn) => void (ls[t] ||= []).push(fn), close: () => undefined };
    };
    const deps = { api, sessionId: "note-web-s", processInline: () => false, update: (_id: string, p: Partial<CaptureItem>) => void states.push(p), sleep: async () => undefined, follow: { eventSource: stream } };
    return { bodies, states, deps };
  }

  it("uploads, starts with the idempotency key, shows the streamed answer, and re-reads the note until processed (M8)", async () => {
    const h = harness(["queued", "queued", "processed"]);
    const file = new File(["hello"], "a.txt", { type: "text/plain" });
    await runCapture({ ...base, files: [{ name: "a.txt", size: 5, type: "text/plain", lastModified: 1541412000000 }], state: "uploading" }, [file], h.deps);
    const start = h.bodies.find((b) => b.url.endsWith("runs/start"))!.body as { input_data: Record<string, unknown>; flow_id: string; bundle_id: string; session_id: string };
    expect(start).toMatchObject({ bundle_id: "abstractnote", flow_id: "note-capture", session_id: "note-web-s" });
    // the file's last change travels with its reference (dates an upload without a date of its own)
    expect(start.input_data).toMatchObject({ session_id: "note-web-s", idempotency_key: "k-123", context: { attachments: [{ $artifact: "art-1", last_modified: 1541412000000 }] } });
    expect(h.states.find((s) => s.answer)).toMatchObject({ answerSource: "stream" });
    expect(h.bodies.filter((b) => b.url.includes("note/notes/nt_x"))).toHaveLength(3);
    const last = h.states[h.states.length - 1];
    expect(last.state).toBe("processed");
    expect(last.note?.entities?.[0].name).toBe("Dana");
  });

  it("shows the flow's refusal sentence without the runtime prefix", async () => {
    const fetch = vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url);
      if (u.endsWith("runs/start")) return new Response(JSON.stringify({ run_id: "r1" }));
      if (u.endsWith("runs/r1")) return new Response(JSON.stringify({ status: "completed", output: { success: false, error: "Runtime error: Local file paths are not accepted here; upload the file as an attachment instead." } }));
      if (u.includes("/view")) return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
      return new Response(JSON.stringify({ items: [], next_after: 0 }));
    });
    const api = createApi({ fetch: fetch as unknown as typeof globalThis.fetch, sleep: async () => undefined, csrf: () => "c" });
    const states: Array<Partial<CaptureItem>> = [];
    await runCapture(base, [], { api, sessionId: "s", processInline: () => false, update: (_i, p) => void states.push(p), follow: { eventSource: undefined, pollMs: [0] } });
    expect(states[states.length - 1]).toMatchObject({ state: "failed", error: "Local file paths are not accepted here; upload the file as an attachment instead." });
  });

  it("a retry after a failed start reuses the same key and the uploaded files", async () => {
    const h = harness(["processed"]);
    await runCapture({ ...base, artifacts: [{ $artifact: "art-0" }], files: [{ name: "a", size: 1, type: "" }] }, [], h.deps);
    expect(h.bodies.some((b) => b.url.endsWith("attachments/upload"))).toBe(false);
    const start = h.bodies.find((b) => b.url.endsWith("runs/start"))!.body as { input_data: { idempotency_key: string; context: unknown } };
    expect(start.input_data.idempotency_key).toBe("k-123");
    expect(start.input_data.context).toMatchObject({ attachments: [{ $artifact: "art-0" }] });
  });
});

describe("first use", () => {
  it("creates the processor automations only when the notebook has none", async () => {
    const { ensureProcessor } = await import("../src/App");
    const calls: string[] = [];
    const mk = (automations: unknown) =>
      createApi({
        fetch: (async (url: RequestInfo | URL, init?: RequestInit) => {
          calls.push(`${init?.method || "GET"} ${String(url)}`);
          return new Response(JSON.stringify(String(url).endsWith("note/status") ? { automations } : { processor_id: "p", reflect_id: "r" }));
        }) as unknown as typeof globalThis.fetch,
        csrf: () => "c",
      });
    expect(await ensureProcessor(mk({ processor_id: null, reflect_id: null }))).toBe(true);
    expect(calls).toContain("POST api/gateway/note/processor/ensure");
    calls.length = 0;
    expect(await ensureProcessor(mk({ processor_id: "p", reflect_id: "r" }))).toBe(false);
    expect(calls.some((c) => c.startsWith("POST"))).toBe(false);
    // W14: an automation the server reports unhealthy (an upgrade left it on the old version) is repaired on load
    const health = (healthy: boolean) => ({ id: "x", state: "active", target: "abstractnote@0.1.0:note-drain", healthy, reason: healthy ? null : "old", paused: false });
    calls.length = 0;
    expect(await ensureProcessor(mk({ processor_id: "p", reflect_id: "r", processor: health(false), reflect: health(true) }))).toBe(true);
    expect(calls).toContain("POST api/gateway/note/processor/ensure");
    calls.length = 0;
    expect(await ensureProcessor(mk({ processor_id: "p", reflect_id: "r", processor: health(true), reflect: health(true) }))).toBe(false);
    expect(calls.some((c) => c.startsWith("POST"))).toBe(false);
  });
});
