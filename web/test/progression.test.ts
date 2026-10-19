import { describe, expect, it } from "vitest";
import { areaPath, countIn, cumulative, inflections, labelRows, linePath, linePoints, nearestIndex, niceMax, periods, progression, thinTicks, weekStart } from "../src/lib/progression";
import { markerWords, monthTicks } from "../src/views/Overview";

const box = { width: 120, height: 60, left: 10, right: 10, top: 10, bottom: 10 };

describe("the line", () => {
  it("spreads values evenly, the maximum at the top of the plot and zero on the baseline", () => {
    const pts = linePoints([0, 5, 10], box, 10);
    expect(pts.map((p) => p.x)).toEqual([10, 60, 110]);
    expect(pts.map((p) => p.y)).toEqual([50, 30, 10]);
    expect(linePath(pts)).toBe("M10,50 L60,30 L110,10");
    expect(areaPath(pts, 50)).toBe("M10,50 L60,30 L110,10 L110,50 L10,50 Z");
  });
  it("an all-zero line lies on the baseline; a single value sits in the middle", () => {
    expect(linePoints([0, 0], box).every((p) => p.y === 50)).toBe(true);
    expect(linePoints([3], box)[0].x).toBe(60);
  });
  it("a scale top that is a plain number close above the maximum", () => {
    expect(niceMax(12)).toBe(15);
    expect(niceMax(20)).toBe(20);
    expect(niceMax(3)).toBe(3);
    expect(niceMax(0.4)).toBe(1);
    expect(niceMax(0)).toBe(1);
    expect(niceMax(87)).toBe(100);
  });
  it("the crosshair snaps to the nearest point", () => {
    const pts = [{ x: 0 }, { x: 10 }, { x: 20 }];
    expect(nearestIndex(pts, 14)).toBe(1);
    expect(nearestIndex(pts, 16)).toBe(2);
    expect(nearestIndex(pts, -5)).toBe(0);
  });
  it("axis ticks closer than the gap are dropped", () => {
    expect(thinTicks([{ x: 0 }, { x: 20 }, { x: 50 }, { x: 70 }, { x: 95 }], 40).map((t) => t.x)).toEqual([0, 50, 95]);
  });
});

describe("weeks that stand out", () => {
  const w = (new_entities: number, claims_superseded: number) => ({ new_entities, claims_superseded });
  it("marks unusually many new entries and any replaced belief, in time order", () => {
    const weeks = [w(0, 0), w(0, 0), w(5, 0), w(3, 0), w(1, 0), w(0, 1), w(0, 0), w(0, 2), w(3, 0)];
    const m = inflections(weeks);
    expect(m.map((x) => x.index)).toEqual([2, 5, 7]); // 3 new is under the mean plus one deviation (3.1)
    expect(m.find((x) => x.index === 7)).toMatchObject({ replaced: true, newEntities: false });
    expect(m.find((x) => x.index === 2)).toMatchObject({ replaced: false, newEntities: true });
  });
  it("a steady notebook has no markers; one new entry a week is not unusual", () => {
    expect(inflections([w(1, 0), w(1, 0), w(1, 0), w(1, 0)])).toEqual([]);
  });
  it("keeps only the strongest when there are too many", () => {
    const weeks = [w(9, 0), w(0, 0), w(4, 0), w(0, 0), w(0, 3), w(0, 0), w(0, 1)];
    const m = inflections(weeks, 2);
    expect(m.map((x) => x.index)).toEqual([0, 4]);
  });
  it("the words above a marker", () => {
    expect(markerWords({ new_entities: 5, claims_superseded: 2 }, { newEntities: true, replaced: true })).toBe("5 new · 2 changed");
    expect(markerWords({ new_entities: 5, claims_superseded: 2 }, { newEntities: false, replaced: true })).toBe("2 changed");
  });
  it("labels share two rows and are dropped only when neither has room", () => {
    expect(labelRows([0, 30, 50, 100, 110], 60)).toEqual([0, 1, -1, 0, 1]);
  });
  it("one month tick per month, on its first week", () => {
    expect(monthTicks(["2026-06-29", "2026-07-06", "2026-07-13", "2026-08-03", "2026-08-10"]).map((t) => t.index)).toEqual([0, 1, 3]);
  });
});

describe("an entity's progression", () => {
  const DAY = 86400_000;
  it("weeks start on Monday (UTC)", () => {
    expect(new Date(weekStart(Date.parse("2026-09-17T10:00:00Z"))).toISOString().slice(0, 10)).toBe("2026-09-14");
    expect(new Date(weekStart(Date.parse("2026-09-14T00:00:00Z"))).toISOString().slice(0, 10)).toBe("2026-09-14");
  });
  it("weekly periods up to half a year, monthly beyond", () => {
    const from = Date.parse("2026-07-23T00:00:00Z");
    expect(periods(from, from + 70 * DAY).every((p) => p.label === "week")).toBe(true);
    const long = periods(from, Date.parse("2027-03-01T00:00:00Z"));
    expect(long[0].label).toBe("month");
    expect(new Date(long[0].start).toISOString().slice(0, 10)).toBe("2026-07-01");
    expect(new Date(long[long.length - 1].start).toISOString().slice(0, 10)).toBe("2027-03-01");
  });
  it("counts per period and running totals", () => {
    const ps = periods(Date.parse("2026-09-14T00:00:00Z"), Date.parse("2026-09-30T00:00:00Z"));
    expect(countIn([Date.parse("2026-09-15T00:00:00Z"), Date.parse("2026-09-16T00:00:00Z"), Date.parse("2026-09-29T00:00:00Z")], ps)).toEqual([2, 0, 1]);
    expect(cumulative([2, 0, 1])).toEqual([2, 2, 3]);
  });
  it("the line is what was stated, the markers are where a belief was replaced, with its words", () => {
    const p = progression(["2026-07-23T14:30:00Z", "2026-07-23T14:30:00Z", "2026-07-30T11:00:00Z", "2026-09-14T10:00:00Z", "2026-10-02T11:00:00Z"], [{ at: "2026-09-14T10:00:00Z", text: "LUT budget: 40k → 25k" }, { at: "2026-10-02T11:00:00Z", text: "LUT status: exploring → stopped" }], Date.parse("2026-10-19T00:00:00Z"));
    expect(p.total[p.total.length - 1]).toBe(5);
    expect(p.added.reduce((a, b) => a + b, 0)).toBe(5);
    expect(p.changes.map((c) => c.texts)).toEqual([["LUT budget: 40k → 25k"], ["LUT status: exploring → stopped"]]);
    expect(p.replaced.filter(Boolean)).toHaveLength(2);
    // A marker is labelled with the day of its change, not the start of its week (2 October, in the week of 28 September).
    expect(new Date(p.changes[1].at).toISOString().slice(0, 10)).toBe("2026-10-02");
    expect(p.total.every((v, i) => i === 0 || v >= p.total[i - 1])).toBe(true);
  });
  it("nothing stated: an empty progression", () => {
    expect(progression([], []).periods).toEqual([]);
  });
});
