/**
 * `npm run dev:mock` follows runs the way the app does against AbstractGateway 0.17.0: the mock serves the
 * replay view (`GET runs/{id}/view` + `/view/stream`), and with `replayView: false` answers those paths with
 * FastAPI's unmatched-route 404, as a gateway before 0.17.0 does.
 */
import * as http from "node:http";
import { afterEach, describe, expect, it } from "vitest";
// @ts-expect-error plain ESM module
import { MOCK_TOKEN, createMockGateway } from "../mock/server.mjs";
import { createApi } from "../src/lib/api";
import { followRun, startFlow } from "../src/lib/runs";

let server: http.Server | null = null;
afterEach(() => server?.close());

async function mockApi(options: Record<string, unknown> = {}) {
  server = http.createServer(createMockGateway({ processDelayMs: 50, ...options }).handler);
  await new Promise<void>((r) => server!.listen(0, "127.0.0.1", () => r()));
  const base = `http://127.0.0.1:${(server!.address() as { port: number }).port}`;
  const login = await (await fetch(`${base}/api/gateway/session/login`, { method: "POST", body: JSON.stringify({ user_id: "t", token: MOCK_TOKEN }) })).json();
  const session = login.session.session_id as string;
  const csrf = login.session.csrf_token as string;
  const calls: string[] = [];
  const f = (u: string, init: RequestInit = {}) => {
    calls.push(String(u).split("?")[0]);
    return fetch(`${base}/${u}`, { ...init, credentials: undefined, headers: { ...((init.headers as Record<string, string>) || {}), "X-AbstractGateway-Session": session, "X-AbstractGateway-CSRF": csrf } });
  };
  return { api: createApi({ fetch: f as unknown as typeof fetch, csrf: () => csrf, sleep: async () => undefined }), calls };
}

const capture = { text: "Met Dana about the garden", occurred_at: "2026-10-28T09:00:00Z", source: { client: "web" }, idempotency_key: "k-mock-view", process_inline: false, context: { attachments: [] } };

describe("the mock gateway's replay view", () => {
  it("a capture is followed through the view: its answer, then its saved output", async () => {
    const { api, calls } = await mockApi();
    const runId = await startFlow(api, "note-capture", "s-1", capture);
    const answers: string[] = [];
    const run = await followRun(api, runId, { onAnswer: (t) => answers.push(t), eventSource: () => { throw new Error("the ledger stream must not be used"); } });
    expect(run.source).toBe("replay_view");
    expect(run.status).toBe("completed");
    expect(typeof run.output?.note_id).toBe("string");
    expect(answers.length).toBe(1);
    expect(calls.some((c) => c.includes("/ledger"))).toBe(false);
  });

  it("a gateway without the view: the ledger path, labeled", async () => {
    const { api, calls } = await mockApi({ replayView: false });
    const runId = await startFlow(api, "note-capture", "s-2", { ...capture, idempotency_key: "k-mock-old" });
    const run = await followRun(api, runId, { eventSource: () => { throw new Error("no EventSource in this test"); } });
    expect(run.source).toBe("ledger_fold");
    expect(run.sourceReason).toMatch(/0\.17\.0/);
    expect(typeof run.output?.note_id).toBe("string");
    expect(calls.some((c) => c.endsWith("/ledger"))).toBe(true);
  });
});
