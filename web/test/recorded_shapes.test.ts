/**
 * The mock gateway answers with the shapes recorded from the hermetic gateway (W10 re-recorded them,
 * untracked/fixtures/gateway/; the copies the web reads are in test/fixtures/recorded/). Keys are compared
 * recursively (objects by key set, lists by their first item), so a mock that drifts from the router goes red.
 */
import * as http from "node:http";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
// @ts-expect-error plain ESM module
import { MOCK_TOKEN, createMockGateway } from "../mock/server.mjs";

const DIR = join(__dirname, "fixtures", "recorded");
const fixture = (name: string) => JSON.parse(readFileSync(join(DIR, `${name}.json`), "utf8")) as { status: number; body: unknown };

/** "a.b[0].c" paths of every key, lists by their first item. */
export function shape(v: unknown, at = ""): string[] {
  if (Array.isArray(v)) return v.length ? shape(v[0], `${at}[0]`) : [];
  if (v && typeof v === "object") return Object.entries(v as Record<string, unknown>).flatMap(([k, x]) => [`${at}.${k}`, ...shape(x, `${at}.${k}`)]);
  return [];
}

/**
 * Where the mock's keys differ from the recording: objects by key set (then each common key), lists by their
 * first items when both have one (a list the recording left empty says nothing about its items).
 */
export function drift(mock: unknown, rec: unknown, at = ""): string[] {
  if (Array.isArray(rec)) {
    if (!Array.isArray(mock)) return [`${at}: not a list`];
    return rec.length && mock.length ? drift(mock[0], rec[0], `${at}[0]`) : [];
  }
  if (rec && typeof rec === "object") {
    if (!mock || typeof mock !== "object" || Array.isArray(mock)) return rec && Object.keys(rec).length && mock !== null ? [`${at}: not an object`] : [];
    const m = mock as Record<string, unknown>;
    const r = rec as Record<string, unknown>;
    const out = [...Object.keys(r).filter((k) => !(k in m)).map((k) => `${at}.${k} missing`), ...Object.keys(m).filter((k) => !(k in r)).map((k) => `${at}.${k} extra`)];
    // `source` is what the client sent ({client, origin?, author?}); its inside varies by client.
    for (const k of Object.keys(r)) if (k in m && k !== "source") out.push(...drift(m[k], r[k], `${at}.${k}`));
    return out;
  }
  return [];
}

let server: http.Server;
let base = "";
const H = { Authorization: `Bearer ${MOCK_TOKEN}`, "Content-Type": "application/json" };
beforeAll(async () => {
  server = http.createServer(createMockGateway({ processDelayMs: 50 }).handler);
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/gateway/note/`;
});
afterAll(() => server.close());

describe("mock follows the recorded gateway", () => {
  it("the comparison sees a missing or an extra key (guard)", () => {
    expect(shape({ a: [{ b: 1, c: { d: 2 } }] })).toEqual([".a", ".a[0].b", ".a[0].c", ".a[0].c.d"]);
    expect(drift({ a: [{ b: 1 }] }, { a: [{ b: 1, c: 2 }] })).toEqual([".a[0].c missing"]);
    expect(drift({ a: [], x: 1 }, { a: [{ b: 1 }] })).toEqual([".x extra"]);
  });

  const cases: Array<[string, string, string?, unknown?]> = [
    ["note_review_200", "review"],
    ["note_queue_200", "queue"],
    ["note_settings_200", "settings"],
    ["note_cues_evidence_200", "cues?limit=1"],
    ["note_entities_include_merged_200", "entities?include_merged=true&limit=1"],
    ["note_entity_as_merged_200", "entities/ex%3Aperson-dana-r?as_merged=true"],
    ["note_changes_as_received_200", "changes?since=2026-01-01T00:00:00Z&as_received=2026-10-10T00:00:00Z"],
    ["note_day_as_received_200", "day/2026-09-14?as_received=2026-10-10T00:00:00Z"],
    ["note_graph_as_received_200", "graph?as_received=2026-10-10T00:00:00Z"],
  ];
  for (const [name, path] of cases) {
    it(`${name}: same keys`, async () => {
      const rec = fixture(name);
      const r = await fetch(`${base}${path}`, { headers: H });
      expect(r.status).toBe(rec.status);
      const body = await r.json();
      if (name === "note_review_200") {
        // One item of each recorded kind against the mock's item of the same kind.
        const kinds = (rec.body as { items: Array<{ kind: string }> }).items;
        expect(kinds.length).toBeGreaterThan(0);
        for (const item of kinds) {
          const mine = body.items.find((i: { kind: string }) => i.kind === item.kind);
          expect(mine, `the mock has no ${item.kind} to review`).toBeTruthy();
          expect(drift(mine, item), item.kind).toEqual([]);
        }
        return;
      }
      expect(drift(body, rec.body)).toEqual([]);
    });
  }

  it("note_get_insight_200 and note_review_confirm_200 / note_entity_as_received_400: same keys and statuses", async () => {
    const cue = (await (await fetch(`${base}cues?limit=1`, { headers: H })).json()).items[0];
    const note = await (await fetch(`${base}notes/${cue.cue_id}`, { headers: H })).json();
    expect(shape(note.insight).sort()).toEqual(shape(fixture("note_get_insight_200").body as Record<string, unknown>).filter((k) => k.startsWith(".insight.")).map((k) => k.slice(".insight".length)).sort());
    const items = (await (await fetch(`${base}review`, { headers: H })).json()).items;
    const merge = items.find((i: { kind: string }) => i.kind === "merge");
    const ok = await fetch(`${base}review/${merge.review_id}/confirm`, { method: "POST", headers: H, body: "{}" });
    expect(ok.status).toBe(200);
    expect(shape(await ok.json()).sort()).toEqual(shape(fixture("note_review_confirm_200").body).sort());
    const early = await fetch(`${base}entities/ex%3Aproject-watch-app?as_received=2026-07-01T00:00:00Z`, { headers: H });
    expect(early.status).toBe(fixture("note_entity_as_received_400").status);
  });
});
