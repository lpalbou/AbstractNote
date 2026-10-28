import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { SseParser, ReplayViewFold, type ReplayView } from "@abstractframework/panel-chat";
import { createApi } from "../src/lib/api";
import { activityWords, followRun, runErrorText } from "../src/lib/runs";

/** A captured case: its frames (as sent) and the view after each frame. */
function load(name: string) {
  const text = readFileSync(new URL(`./fixtures/replay_view/${name}.sse`, import.meta.url), "utf8");
  const frames = new SseParser().push(text);
  const fold = new ReplayViewFold();
  const views = frames.map((f) => {
    fold.apply(f);
    return JSON.parse(JSON.stringify(fold.view)) as ReplayView;
  });
  return { frames, views };
}

const sse = (frames: Array<{ event: string; data: string; id?: string }>) =>
  frames.map((f) => `${f.id ? `id: ${f.id}\n` : ""}event: ${f.event}\ndata: ${f.data}\n\n`).join("");

/** A gateway 0.17.0: GET /view at frame `at`, the stream sends the later frames (then `done` when asked). */
function gateway(name: string, at: number, { done = true, run = { status: "completed", output: { response: "ok", note_id: "nt_1" } } as Record<string, unknown>, viewStatus = 200 } = {}) {
  const { frames, views } = load(name);
  const root = views[0].root_run_id;
  const calls: string[] = [];
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url).replace(/^api\/gateway\//, "").split("?")[0];
    calls.push(`${init?.method || "GET"} ${path}`);
    if (path === `runs/${root}/view`) {
      if (viewStatus !== 200) return new Response(JSON.stringify({ detail: "boom" }), { status: viewStatus });
      return new Response(JSON.stringify(views[at]), { status: 200, headers: { ETag: `"${views[at].cursor.token}"` } });
    }
    if (path === `runs/${root}/view/stream`) {
      const later = frames.slice(at + 1);
      return new Response(sse(done ? [...later, { event: "done", data: "{}" }] : later), { status: 200, headers: { "Content-Type": "text/event-stream" } });
    }
    if (path === `runs/${root}`) return new Response(JSON.stringify({ run_id: root, ...run }), { status: 200 });
    return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
  });
  const api = createApi({ fetch: fetch as unknown as typeof globalThis.fetch, sleep: async () => undefined });
  return { api, calls, root, views, frames };
}

describe("followRun through the replay view (gateway 0.17.0)", () => {
  it("shows the answer from the view and reads the terminal run once", async () => {
    const gw = gateway("l_answer_then_completion", 5);
    const onAnswer = vi.fn();
    const run = await followRun(gw.api, gw.root, { onAnswer });
    expect(onAnswer).toHaveBeenCalledWith("Hello there.", "view");
    expect(onAnswer).toHaveBeenCalledTimes(1);
    expect(run.source).toBe("replay_view");
    expect(run.output?.note_id).toBe("nt_1");
    expect(gw.calls).toEqual([`GET runs/${gw.root}/view`, `GET runs/${gw.root}`]);
  });

  it("reports a run that waits for a person instead of following it for ever", async () => {
    const gw = gateway("a_delegated_child_approval", 21);
    const run = await followRun(gw.api, gw.root);
    expect(run.status).toBe("waiting");
    expect(run.waiting).toMatchObject({ kind: "tool_approval", run_id: gw.views[21].interaction!.run_id });
    expect(runErrorText(run)).toMatch(/^This run is waiting for an approval/);
    expect(gw.calls).toEqual([`GET runs/${gw.root}/view`]);
  });

  it("follows the stream until the view names the wait (event wait with a prompt)", async () => {
    const gw = gateway("i_wait_kinds", 0, { done: false });
    expect(gw.views[0].interaction).toBeNull();
    const run = await followRun(gw.api, gw.root);
    expect(run.status).toBe("waiting");
    expect(run.waiting).toMatchObject({ kind: "event", prompt: "Which colour should the chart use?" });
    expect(runErrorText(run)).toContain("“Which colour should the chart use?”");
    expect(gw.calls.filter((c) => c.endsWith("/view/stream"))).toHaveLength(1);
  });

  it("a view read that fails is an error, never a silent fallback to the ledger", async () => {
    const gw = gateway("l_answer_then_completion", 5, { viewStatus: 500 });
    await expect(followRun(gw.api, gw.root)).rejects.toThrow(/boom/);
    expect(gw.calls.some((c) => c.includes("/ledger"))).toBe(false);
  });

  it("a stream that closes without frames is retried a bounded number of times", async () => {
    const gw = gateway("i_wait_kinds", 16, { done: false });
    // frame 16 already names the wait: settle without a stream at all.
    expect((await followRun(gw.api, gw.root)).status).toBe("waiting");
    const idle = gateway("l_answer_then_completion", 0, { done: false });
    // A view still open with no later frames: the closed stream is reconnected, then given up.
    const open = JSON.parse(JSON.stringify(idle.views[0]));
    open.status = "running";
    idle.views[0] = open;
    idle.frames.length = 1;
    await expect(followRun(idle.api, idle.root, { viewRetries: 3, sleep: async () => undefined })).rejects.toThrow(/stopped answering/);
    expect(idle.calls.filter((c) => c.endsWith("/view/stream"))).toHaveLength(3);
  });

  it("labels the ledger fallback on a gateway without the view", async () => {
    const fetch = vi.fn(async (url: RequestInfo | URL) => {
      const path = String(url).replace(/^api\/gateway\//, "").split("?")[0];
      if (path === "runs/r9") return new Response(JSON.stringify({ status: "completed", output: { note_id: "nt_9" } }), { status: 200 });
      if (path === "runs/r9/ledger") return new Response(JSON.stringify({ items: [], next_after: 0 }), { status: 200 });
      return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
    });
    const api = createApi({ fetch: fetch as unknown as typeof globalThis.fetch, sleep: async () => undefined });
    const run = await followRun(api, "r9", { eventSource: () => { throw new Error("no stream"); } });
    expect(run.source).toBe("ledger_fold");
    expect(run.sourceReason).toMatch(/0\.17\.0/);
    expect(run.output?.note_id).toBe("nt_9");
  });
});

describe("activityWords", () => {
  it("says what a finished step did, in the ledger fold's words", () => {
    const base = { id: "a", run_id: "r", status: "completed" } as const;
    expect(activityWords({ ...base, kind: "tool", tool_calls: [{ name: "note_search" }] })).toBe("Searched your notes");
    expect(activityWords({ ...base, kind: "tool", tool_calls: [{ name: "other" }] })).toBeNull();
    expect(activityWords({ ...base, kind: "llm" })).toBe("Wrote the answer");
    expect(activityWords({ ...base, kind: "llm", status: "started" })).toBeNull();
    expect(activityWords({ ...base, kind: "subworkflow", status: "running" })).toBe("Dug through your history");
  });
});
