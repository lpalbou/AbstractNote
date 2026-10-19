/**
 * The mock gateway serves the pinned shapes of docs/api.md ("Pinned response shapes"), so a
 * screen that works against the mock decodes the real router. Keys are read from api.md itself:
 * if the doc changes, this goes red until the mock follows.
 */
import * as http from "node:http";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
// @ts-expect-error plain ESM module
import { MOCK_TOKEN, createMockGateway } from "../mock/server.mjs";

const API_MD_FULL = readFileSync(join(__dirname, "..", "..", "docs", "api.md"), "utf8");
// Only the "Pinned response shapes" table: the read-API table above it has rows with the same names.
const PIN_AT = API_MD_FULL.indexOf("Pinned response shapes");
if (PIN_AT < 0) throw new Error('docs/api.md has no "Pinned response shapes" section');
const API_MD = API_MD_FULL.slice(PIN_AT);
let server: http.Server;
let base = "";
let session = "";
let csrf = "";

function pinned(endpoint: string): string {
  const row = API_MD.split("\n").find((l) => l.startsWith("|") && l.split("|")[1].replace(/`/g, "").trim() === endpoint);
  if (!row) throw new Error(`docs/api.md has no pinned row for ${endpoint}`);
  return row.split("|")[2];
}
/** The shape text of a pinned row: the first `...` span of its second cell. */
function shapeOf(cell: string): string {
  const m = cell.match(/`([^`]+)`/);
  if (!m) throw new Error(`no shape in ${cell}`);
  return m[1];
}
/** Required top-level keys of `{a, b: [...], c: {...}, d?}` (optional `d?` left out). */
function topKeys(cell: string): string[] {
  const s = shapeOf(cell);
  const inner = s.slice(s.indexOf("{") + 1, s.lastIndexOf("}"));
  const keys: string[] = [];
  let depth = 0;
  let cur = "";
  for (const ch of inner) {
    if ("[{(".includes(ch)) depth++;
    if ("]})".includes(ch)) depth--;
    if (ch === "," && depth === 0) {
      keys.push(cur);
      cur = "";
    } else cur += ch;
  }
  keys.push(cur);
  const out = keys.map((k) => k.split(":")[0].trim()).filter((k) => /^[a-z_]+$/.test(k));
  if (!out.length) throw new Error(`could not read the keys of the pinned shape ${s}`);
  return out;
}
/** Keys of the list items of `{items: [{...}]}`. */
function itemKeys(cell: string): string[] {
  const s = shapeOf(cell);
  const at = s.indexOf("[{");
  if (at < 0) throw new Error(`no item shape in ${s}`);
  let depth = 0;
  let end = at + 1;
  for (; end < s.length; end++) {
    if (s[end] === "{") depth++;
    if (s[end] === "}") depth--;
    if (depth === 0) break;
  }
  return topKeys(`\`${s.slice(at + 1, end + 1)}\``);
}
async function call(path: string, method = "GET", body?: unknown) {
  const r = await fetch(`${base}${path}`, { method, headers: { "X-AbstractGateway-Session": session, "X-AbstractGateway-CSRF": csrf, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: await r.json() };
}

beforeAll(async () => {
  server = http.createServer(createMockGateway({ processDelayMs: 50 }).handler);
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const login = await (await fetch(`${base}/api/gateway/session/login`, { method: "POST", body: JSON.stringify({ user_id: "t", token: MOCK_TOKEN }) })).json();
  session = login.session.session_id;
  csrf = login.session.csrf_token;
});
afterAll(() => server.close());

describe("mock gateway serves the pinned shapes", () => {
  const cases: Array<[string, string, (b: Record<string, unknown>) => unknown]> = [
    ["GET /note/changes", "/api/gateway/note/changes?since=2026-01-01T00:00:00Z", (b) => b],
    ["GET /note/overview", "/api/gateway/note/overview?days=400", (b) => b],
    ["GET /note/cues", "/api/gateway/note/cues", (b) => b],
    ["GET /note/tasks", "/api/gateway/note/tasks", (b) => b],
  ];
  for (const [endpoint, path] of cases) {
    it(`${endpoint} has exactly the pinned top-level keys`, async () => {
      const { status, body } = await call(path);
      expect(status).toBe(200);
      const keys = topKeys(pinned(endpoint));
      expect(Object.keys(body).filter((k) => k !== "next_cursor").sort()).toEqual(keys.sort());
    });
  }

  it("list items and nested refs follow the pinned item shapes", async () => {
    const changes = (await call("/api/gateway/note/changes?since=2026-01-01T00:00:00Z")).body;
    expect(Object.keys(changes.notes[0]).sort()).toEqual(topKeys(pinned("NoteRef")).sort());
    expect(Object.keys(changes.insights[0]).sort()).toEqual(topKeys(pinned("Insight")).sort());
    expect(Object.keys(changes.superseded_claims[0]).sort()).toEqual(["claim_id", "note_id", "superseded_by", "text"]);
    const cue = (await call("/api/gateway/note/cues")).body.items[0];
    expect(Object.keys(cue).sort()).toEqual(itemKeys(pinned("GET /note/cues")).sort());
    expect(Object.keys(cue.subject).sort()).toEqual(["id", "kind", "title"]);
    const task = (await call("/api/gateway/note/tasks")).body.items[0];
    expect(Object.keys(task).sort()).toEqual(itemKeys(pinned("GET /note/tasks")).sort());
  });

  it("GET /note/notes/{id} has the pinned keys", async () => {
    const id = (await call("/api/gateway/note/changes?since=2026-01-01T00:00:00Z")).body.notes[0].note_id;
    const note = (await call(`/api/gateway/note/notes/${id}`)).body;
    expect(Object.keys(note).sort()).toEqual(topKeys(pinned("GET /note/notes/{id}")).sort());
  });

  it("note-capture answers through answer_user and output.response, and links context.about", async () => {
    const start = await call("/api/gateway/runs/start", "POST", { bundle_id: "abstractnote", flow_id: "note-capture", session_id: "note-web-t", input_data: { text: "A thought", session_id: "note-web-t", idempotency_key: "k1", context: { attachments: [], about: { kind: "entity", id: "ex:person-dana", title: "Dana" } } } });
    const runId = start.body.run_id;
    let run;
    for (let i = 0; i < 50; i++) {
      run = (await call(`/api/gateway/runs/${runId}`)).body;
      if (run.status === "completed") break;
      await new Promise((r) => setTimeout(r, 100));
    }
    expect(Object.keys(run.output).sort()).toEqual(expect.arrayContaining(topKeys(pinned("note-capture output")).sort()));
    const ledger = (await call(`/api/gateway/runs/${runId}/ledger?after=0`)).body.items.map((i: { record: { effect: { type: string }; result: { message?: string } } }) => i.record);
    const answer = ledger.find((r: { effect: { type: string } }) => r.effect.type === "answer_user");
    expect(answer.result.message).toBe(run.output.response);
    await new Promise((r) => setTimeout(r, 200));
    const note = (await call(`/api/gateway/note/notes/${run.output.note_id}`)).body;
    expect(note.entities.map((e: { entity_id: string }) => e.entity_id)).toContain("ex:person-dana");
    const again = await call("/api/gateway/runs/start", "POST", { bundle_id: "abstractnote", flow_id: "note-capture", session_id: "note-web-t", input_data: { text: "A thought", session_id: "note-web-t", idempotency_key: "k1" } });
    let dup;
    for (let i = 0; i < 30; i++) {
      dup = (await call(`/api/gateway/runs/${again.body.run_id}`)).body;
      if (dup.status === "completed") break;
      await new Promise((r) => setTimeout(r, 100));
    }
    expect(dup.output).toMatchObject({ note_id: run.output.note_id, status: "duplicate" });
  });
});
