/**
 * The shapes W17 serves for the Overview and the community colours (lead brief W16, 2026-10-19), and the
 * mock gateway serving exactly those shapes, key for key, at every level:
 *   GET /note/communities, GET /note/stats, graph nodes' community_id, the entity card's community.
 * When docs/api.md pins these rows, SPEC below must be read from it (seam, untracked/reports/W16.md).
 */
import * as http from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
// @ts-expect-error plain ESM module
import { MOCK_TOKEN, createMockGateway } from "../mock/server.mjs";

/** A shape: "string" | "number" | "boolean" | "string|null" | {key: shape} | [itemShape] | {"*": shape} (a map). */
type Shape = string | Shape[] | { [k: string]: Shape };

const ENTITY = { entity_id: "string", name: "string", kind: "string", note_count: "number" };
export const SPEC: Record<string, Shape> = {
  communities: { items: [{ community_id: "string", label: "string", summary: "string", kind: "string", size: "number", top_entities: [ENTITY], stale_label: "boolean", labelled_at: "string" }] },
  stats: {
    totals: { notes_by_kind: { "*": "number" }, notes_by_source: { "*": "number" }, attachments_by_mime: { "*": "number" } },
    activity_weekly: [{ week: "string", notes: "number", revisions: "number", new_entities: "number", claims_added: "number", claims_superseded: "number" }],
    entities_by_kind: { "*": "number" },
    top_entities: [{ ...ENTITY, first_seen: "string", last_seen: "string" }],
    claims_by_predicate: [{ predicate: "string", count: "number" }],
    tasks: { open: "number", closed: "number" },
    insights_by_kind: { "*": "number" },
    communities: "number",
    processing: { understood: "number", queued: "number", failed: "number" },
    growth_monthly: [{ month: "string", entities: "number", claims: "number" }],
    quiet_entities: [{ entity_id: "string", name: "string", last_seen: "string" }],
  },
  entityCommunity: { community_id: "string", label: "string" },
};

/** Every mismatch between a value and a shape, as "path: why" (empty = it matches). */
export function mismatches(value: unknown, shape: Shape, path = "$"): string[] {
  if (typeof shape === "string") {
    const ok = shape.split("|").some((t) => (t === "null" ? value === null : typeof value === t));
    return ok ? [] : [`${path}: expected ${shape}, got ${value === null ? "null" : typeof value}`];
  }
  if (Array.isArray(shape)) {
    if (!Array.isArray(value)) return [`${path}: expected a list`];
    if (!value.length) return [`${path}: empty list (nothing to check)`];
    return value.flatMap((v, i) => mismatches(v, shape[0], `${path}[${i}]`));
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return [`${path}: expected an object`];
  const obj = value as Record<string, unknown>;
  if ("*" in shape) {
    if (!Object.keys(obj).length) return [`${path}: empty map (nothing to check)`];
    return Object.entries(obj).flatMap(([k, v]) => mismatches(v, shape["*"], `${path}.${k}`));
  }
  const want = Object.keys(shape).sort();
  const got = Object.keys(obj).sort();
  const out: string[] = [];
  if (want.join(",") !== got.join(",")) out.push(`${path}: keys ${got.join(",")} != ${want.join(",")}`);
  for (const k of want) if (k in obj) out.push(...mismatches(obj[k], shape[k], `${path}.${k}`));
  return out;
}

let server: http.Server;
let base = "";
let session = "";
const get = async (path: string) => {
  const r = await fetch(`${base}/api/gateway/${path}`, { headers: { "X-AbstractGateway-Session": session } });
  return { status: r.status, body: await r.json() };
};

beforeAll(async () => {
  server = http.createServer(createMockGateway({ processDelayMs: 50 }).handler);
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const login = await (await fetch(`${base}/api/gateway/session/login`, { method: "POST", body: JSON.stringify({ user_id: "t", token: MOCK_TOKEN }) })).json();
  session = login.session.session_id;
});
afterAll(() => server.close());

describe("the shape checker (guard against a silent check)", () => {
  it("reports a missing key, an extra key, a wrong type and an empty list", () => {
    expect(mismatches({ a: 1 }, { a: "number", b: "string" })).toHaveLength(1);
    expect(mismatches({ a: 1, c: 2 }, { a: "number" })).toHaveLength(1);
    expect(mismatches({ a: "1" }, { a: "number" })).toHaveLength(1);
    expect(mismatches({ a: [] }, { a: ["number"] })).toHaveLength(1);
    expect(mismatches({ a: { x: 1, y: "2" } }, { a: { "*": "number" } })).toHaveLength(1);
    expect(mismatches({ a: [1, 2] }, { a: ["number"] })).toEqual([]);
  });
});

describe("the mock serves the W17 shapes", () => {
  it("GET /note/communities: six labelled communities, one stale", async () => {
    const { status, body } = await get("note/communities");
    expect(status).toBe(200);
    expect(mismatches(body, SPEC.communities)).toEqual([]);
    expect(body.items).toHaveLength(6);
    expect(body.items.filter((c: { stale_label: boolean }) => c.stale_label)).toHaveLength(1);
    expect(body.items.every((c: { label: string }) => c.label.length > 0)).toBe(true);
  });

  it("GET /note/stats: every field, 16 weeks of activity, counts that agree", async () => {
    const { status, body } = await get("note/stats");
    expect(status).toBe(200);
    expect(mismatches(body, SPEC.stats)).toEqual([]);
    expect(body.activity_weekly).toHaveLength(16);
    const weeks = body.activity_weekly.map((w: { week: string }) => Date.parse(`${w.week}T00:00:00Z`));
    expect(weeks.every((t: number, i: number) => i === 0 || t - weeks[i - 1] === 7 * 86400_000)).toBe(true);
    expect(new Date(weeks[0]).getUTCDay()).toBe(1);
    expect(body.communities).toBe((await get("note/communities")).body.items.length);
    const notes = Object.values(body.totals.notes_by_kind as Record<string, number>).reduce((a, b) => a + b, 0);
    expect(body.processing.understood + body.processing.queued + body.processing.failed).toBe(notes);
  });

  it("graph nodes carry community_id and the entity card its community", async () => {
    const g = (await get("note/graph")).body;
    expect(g.nodes.length).toBeGreaterThan(0);
    expect(g.nodes.every((n: Record<string, unknown>) => typeof n.community_id === "string")).toBe(true);
    const ids = new Set((await get("note/communities")).body.items.map((c: { community_id: string }) => c.community_id));
    expect(g.nodes.every((n: { community_id: string }) => ids.has(n.community_id))).toBe(true);
    const card = (await get("note/entities/ex:person-dana")).body;
    expect(mismatches(card.community, SPEC.entityCommunity)).toEqual([]);
  });
});
