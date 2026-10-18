import { describe, expect, it, vi } from "vitest";
import { createApi } from "../src/lib/api";
import { answerFromRecord, followRun, recordFromFrame, type EventSourceLike } from "../src/lib/runs";

const answerRecord = (message: string) => ({ effect: { type: "answer_user", payload: { message } }, status: "completed", result: { message, level: "message" } });

function fakeApi(routes: Record<string, unknown[]>) {
  const fetch = vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url).replace(/^api\/gateway\//, "").split("?")[0];
    const queue = routes[path];
    if (!queue || !queue.length) return new Response(JSON.stringify({ detail: "no" }), { status: 404 });
    const body = queue.length > 1 ? queue.shift() : queue[0];
    return new Response(JSON.stringify(body), { status: 200 });
  });
  return { api: createApi({ fetch: fetch as unknown as typeof globalThis.fetch, sleep: async () => undefined }), fetch };
}

function fakeStream(frames: Array<[string, unknown]>, fail = false): (url: string) => EventSourceLike {
  return (url) => {
    const listeners: Record<string, Array<(ev: MessageEvent) => void>> = {};
    const es: EventSourceLike & { url: string } = {
      url,
      onerror: null,
      addEventListener: (t, fn) => {
        (listeners[t] ||= []).push(fn);
      },
      close: () => undefined,
    };
    setTimeout(() => {
      if (fail) return es.onerror?.(new Event("error"));
      for (const [type, data] of frames) for (const fn of listeners[type] || []) fn({ data: JSON.stringify(data) } as MessageEvent);
    }, 0);
    return es;
  };
}

describe("ledger records", () => {
  it("finds the answer_user message and nothing else", () => {
    expect(answerFromRecord(answerRecord("Saved as idea."))).toBe("Saved as idea.");
    expect(answerFromRecord({ effect: { type: "llm_call" }, status: "completed", result: { message: "x" } })).toBeNull();
    expect(answerFromRecord({ ...answerRecord("x"), status: "started" })).toBeNull();
    expect(recordFromFrame(JSON.stringify({ cursor: 3, record: answerRecord("y") }))).toEqual({ cursor: 3, record: answerRecord("y") });
    expect(recordFromFrame("not json")).toBeNull();
  });
});

describe("followRun", () => {
  it("shows the answer from the stream, then reads the terminal run", async () => {
    const { api } = fakeApi({ "runs/r1": [{ status: "completed", output: { response: "Saved as idea.", note_id: "nt_a", status: "queued" } }] });
    const onAnswer = vi.fn();
    const run = await followRun(api, "r1", {
      onAnswer,
      eventSource: fakeStream([
        ["step", { cursor: 1, record: { effect: { type: "tool_calls" }, status: "completed" } }],
        ["step", { cursor: 2, record: answerRecord("Saved as idea.") }],
        ["done", { status: "terminal" }],
      ]),
    });
    expect(onAnswer).toHaveBeenCalledWith("Saved as idea.", "stream");
    expect(run.output?.note_id).toBe("nt_a");
  });

  it("falls back to polling the run and its ledger pages when the stream fails", async () => {
    const { api } = fakeApi({
      "runs/r2/ledger": [{ items: [{ cursor: 1, record: answerRecord("Saved.") }], next_after: 1 }],
      "runs/r2": [{ status: "running" }, { status: "completed", output: { response: "Saved." } }],
    });
    const onAnswer = vi.fn();
    const run = await followRun(api, "r2", { onAnswer, eventSource: fakeStream([], true), pollMs: [0] });
    expect(onAnswer).toHaveBeenCalledWith("Saved.", "ledger");
    expect(run.status).toBe("completed");
  });

  it("streams from a relative ledger URL", async () => {
    const urls: string[] = [];
    const { api } = fakeApi({ "runs/r3": [{ status: "completed", output: {} }] });
    await followRun(api, "r3", {
      eventSource: (u) => {
        urls.push(u);
        return fakeStream([["done", {}]])(u);
      },
    });
    expect(urls).toEqual(["api/gateway/runs/r3/ledger/stream?after=0"]);
  });
});
