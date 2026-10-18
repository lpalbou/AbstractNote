import { describe, expect, it } from "vitest";
import { layoutGraph, nodeRadius } from "../src/lib/graph_layout";
import { bucketCounts, isoToScrub, scrubToIso, sparkline } from "../src/lib/sparkline";

const DAY = 86400_000;
const from = Date.parse("2026-07-01T00:00:00Z");
const to = from + 70 * DAY;

describe("sparkline", () => {
  it("counts dates into equal buckets and ignores dates outside", () => {
    const iso = (d: number) => new Date(from + d * DAY).getTime();
    expect(bucketCounts([iso(0), iso(1), iso(15), iso(69), iso(-3), iso(80)], from, to, 10)).toEqual([2, 0, 1, 0, 0, 0, 0, 0, 0, 1]);
  });

  it("puts the busiest bucket at the top and empty buckets on the baseline", () => {
    const dates = ["2026-07-02T00:00:00Z", "2026-07-03T00:00:00Z", "2026-08-20T00:00:00Z"];
    const s = sparkline(dates, { from, to, buckets: 10, width: 100, height: 20, pad: 0 });
    expect(s.max).toBe(2);
    expect(s.points[0]).toEqual([0, 0]);
    expect(s.points[1]).toEqual([11.11, 20]);
    expect(s.points[9][0]).toBe(100);
    expect(s.path.startsWith("M0,0 L11.11,20")).toBe(true);
    expect(s.area.endsWith("Z")).toBe(true);
  });

  it("maps the scrubber to a moment and back; the right end is now", () => {
    expect(scrubToIso(1, from, to)).toBeNull();
    expect(scrubToIso(0, from, to)).toBe("2026-07-01T00:00:00Z");
    const mid = scrubToIso(0.5, from, to)!;
    expect(mid).toBe("2026-08-05T00:00:00Z");
    expect(isoToScrub(mid, from, to)).toBeCloseTo(0.5, 6);
    expect(isoToScrub(null, from, to)).toBe(1);
  });
});

describe("graph layout", () => {
  const nodes = ["a", "b", "c", "d", "e", "f"].map((id, i) => ({ id, weight: i }));
  const edges = [
    { source: "a", target: "b" },
    { source: "b", target: "c" },
    { source: "a", target: "c" },
    { source: "d", target: "e" },
  ];
  it("is deterministic and stays inside the canvas", () => {
    const one = layoutGraph({ nodes, edges, width: 600, height: 400 });
    const two = layoutGraph({ nodes: [...nodes].reverse(), edges, width: 600, height: 400 });
    const byId = (ps: typeof one) => Object.fromEntries(ps.map((p) => [p.id, [p.x, p.y]]));
    expect(byId(one)).toEqual(byId(two));
    for (const p of one) {
      expect(p.x).toBeGreaterThanOrEqual(40);
      expect(p.x).toBeLessThanOrEqual(560);
      expect(p.y).toBeGreaterThanOrEqual(40);
      expect(p.y).toBeLessThanOrEqual(360);
    }
  });
  it("keeps linked entities closer than unlinked ones", () => {
    const ps = Object.fromEntries(layoutGraph({ nodes, edges, width: 800, height: 600 }).map((p) => [p.id, p]));
    const dist = (a: string, b: string) => Math.hypot(ps[a].x - ps[b].x, ps[a].y - ps[b].y);
    expect(dist("d", "e")).toBeLessThan(dist("d", "a"));
    expect(dist("a", "b")).toBeLessThan(dist("a", "f"));
  });
  it("sizes nodes by how often they come up", () => {
    expect(nodeRadius(0)).toBeLessThan(nodeRadius(9));
    expect(nodeRadius(10_000)).toBe(22);
  });
});
