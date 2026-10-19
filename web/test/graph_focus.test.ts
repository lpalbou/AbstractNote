import { describe, expect, it } from "vitest";
import { CARD_DELAY_MS, EDGE_LABEL_ZOOM, MIN_LABEL_EDGE_PX, communitySlots, edgeKey, edgeLabelShown, firstSentence, litBy, placeCard, radiusScale, strongestLinks } from "../src/lib/graph_focus";
import { layoutGraph } from "../src/lib/graph_layout";

const E = (source: string, target: string, predicate: string, count = 1, active = true, last_seen = "2026-09-01T00:00:00Z") => ({ source, target, predicate, count, active, last_seen });
const edges = [E("lut", "dana", "ex:led_by", 1), E("lut", "mlx", "ex:uses", 2), E("priya", "lut", "schema:knowsAbout", 1), E("priya", "mlx", "schema:knowsAbout"), E("dana", "paris", "schema:workLocation"), E("dana", "lyon", "schema:workLocation", 1, false)];

describe("hover: what stays at full strength", () => {
  it("nothing hovered: nothing fades", () => {
    expect(litBy(null, edges)).toBeNull();
  });
  it("a node keeps itself, its edges and its neighbours, and nothing else", () => {
    const lit = litBy({ kind: "node", id: "lut" }, edges)!;
    expect([...lit.nodes].sort()).toEqual(["dana", "lut", "mlx", "priya"]);
    expect([...lit.edges].sort()).toEqual([edgeKey(edges[0]), edgeKey(edges[1]), edgeKey(edges[2])].sort());
    expect(lit.nodes.has("paris")).toBe(false);
    expect(lit.edges.has(edgeKey(edges[3]))).toBe(false);
  });
  it("an edge keeps itself and its two ends", () => {
    const lit = litBy({ kind: "edge", key: edgeKey(edges[4]), source: "dana", target: "paris" }, edges)!;
    expect([...lit.nodes].sort()).toEqual(["dana", "paris"]);
    expect([...lit.edges]).toEqual([edgeKey(edges[4])]);
  });
});

describe("edge words", () => {
  it("hidden at the fitted zoom, shown past the threshold or when lit, never on a faded edge", () => {
    expect(edgeLabelShown(1, false, false)).toBe(false);
    expect(edgeLabelShown(EDGE_LABEL_ZOOM, false, false)).toBe(true);
    expect(edgeLabelShown(0.5, true, false)).toBe(true);
    expect(edgeLabelShown(3, false, true)).toBe(false);
    expect(edgeLabelShown(3, true, false, MIN_LABEL_EDGE_PX - 1)).toBe(false);
    expect(edgeLabelShown(3, true, false, MIN_LABEL_EDGE_PX)).toBe(true);
  });
  it("the card waits 300 ms", () => {
    expect(CARD_DELAY_MS).toBe(300);
  });
});

describe("hover card placement", () => {
  const area = { left: 0, top: 0, width: 1280, height: 800 };
  it("below-right of the pointer when it fits", () => {
    expect(placeCard(100, 100, 300, 160, area)).toEqual({ x: 114, y: 114 });
  });
  it("flips left at the right edge and above at the bottom edge", () => {
    expect(placeCard(1200, 750, 300, 160, area)).toEqual({ x: 1200 - 14 - 300, y: 750 - 14 - 160 });
  });
  it("never leaves the area, even on a narrow phone", () => {
    const phone = { left: 0, top: 0, width: 400, height: 860 };
    for (const [px, py] of [[5, 5], [200, 430], [395, 855], [390, 10]]) {
      const p = placeCard(px, py, 320, 200, phone);
      expect(p.x).toBeGreaterThanOrEqual(8);
      expect(p.y).toBeGreaterThanOrEqual(8);
      expect(p.x + 320).toBeLessThanOrEqual(392);
      expect(p.y + 200).toBeLessThanOrEqual(852);
    }
  });
  it("a card larger than the area pins to its top-left margin", () => {
    expect(placeCard(50, 50, 500, 900, { left: 0, top: 0, width: 400, height: 860 })).toEqual({ x: 8, y: 8 });
  });
});

describe("the strongest links in words", () => {
  const names = new Map([["lut", "LUT"], ["dana", "Dana"], ["mlx", "MLX"], ["priya", "Priya"], ["paris", "Paris"], ["lyon", "Lyon"]]);
  it("strongest first (believed, then most said, then most recent), three at most, both directions in words", () => {
    expect(strongestLinks("lut", "LUT", edges, names)).toEqual(["uses MLX (2 times)", "led by Dana", "Priya knows about LUT"]);
  });
  it("a link no longer believed comes after the believed ones and says so", () => {
    expect(strongestLinks("dana", "Dana", edges, names, 3)).toEqual(["LUT led by Dana", "work location Paris", "work location Lyon, no longer believed"]);
  });
});

describe("summary sentence", () => {
  it("keeps the first sentence and cuts a long one at a word", () => {
    expect(firstSentence("Leads LUT. Moved to Paris.")).toBe("Leads LUT.");
    expect(firstSentence("")).toBe("");
    const long = firstSentence("word ".repeat(80), 40);
    expect(long.length).toBeLessThanOrEqual(40);
    expect(long.endsWith("…")).toBe(true);
    expect(firstSentence("Version 0.11 shipped. Then more.")).toBe("Version 0.11 shipped.");
  });
});

describe("node size by note count", () => {
  it("square-root scale: four times the notes, twice the extra radius; bounded", () => {
    const r = radiusScale(16, 6, 22);
    expect(r(0)).toBe(6);
    expect(r(16)).toBe(22);
    expect(r(4)).toBe(14);
    expect(r(1)).toBe(10);
    expect(r(100)).toBe(22);
    expect(r(undefined)).toBe(6);
  });
  it("the layout uses the given radius", () => {
    const out = layoutGraph({ nodes: [{ id: "a", r: 19 }, { id: "b", weight: 1 }], edges: [], width: 400, height: 300 });
    expect(out.find((n) => n.id === "a")!.r).toBe(19);
  });
});

describe("community colours", () => {
  const cs = [
    { community_id: "c-small", label: "Zed", size: 1 },
    { community_id: "c-big", label: "Big", size: 9 },
    { community_id: "c-mid-b", label: "Beta", size: 4 },
    { community_id: "c-mid-a", label: "Alpha", size: 4 },
    { community_id: "c5", label: "E", size: 2 },
    { community_id: "c6", label: "F", size: 2 },
    { community_id: "c7", label: "G", size: 2 },
  ];
  it("largest first, ties by label; past the sixth they share 'other' (0)", () => {
    const s = communitySlots(cs);
    expect(s.get("c-big")).toBe(1);
    expect(s.get("c-mid-a")).toBe(2);
    expect(s.get("c-mid-b")).toBe(3);
    expect([s.get("c5"), s.get("c6"), s.get("c7")]).toEqual([4, 5, 6]);
    expect(s.get("c-small")).toBe(0);
  });
  it("a community keeps its colour whatever order the list arrives in", () => {
    const a = communitySlots(cs);
    const b = communitySlots([...cs].reverse());
    expect([...b.entries()].sort()).toEqual([...a.entries()].sort());
  });
});

describe("theme names", () => {
  it("a community not named yet reads in plain words", async () => {
    const { themeName } = await import("../src/lib/graph_focus");
    expect(themeName({ label: "LUT prefill" })).toBe("LUT prefill");
    expect(themeName({ label: null })).toBe("A theme not named yet");
    expect(themeName(null)).toBe("A theme not named yet");
  });
});

describe("the community palette in styles.css", () => {
  it("defines one colour per slot and a class for each slot and for 'other'", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const { COMMUNITY_SLOTS } = await import("../src/lib/graph_focus");
    const css = readFileSync(join(__dirname, "..", "src", "styles.css"), "utf8");
    for (let i = 0; i <= COMMUNITY_SLOTS; i++) {
      expect(css).toMatch(new RegExp(`--nt-c${i}:`));
      expect(css).toMatch(new RegExp(`\\.nt-c${i} \\{ --nt-cc: var\\(--nt-c${i}\\); \\}`));
    }
    expect(css).not.toMatch(new RegExp(`--nt-c${COMMUNITY_SLOTS + 1}:`));
  });
});

describe("the hover card keeps clear of what it describes", () => {
  const area = { left: 0, top: 0, width: 1280, height: 800 };
  const hits = (p: { x: number; y: number }, w: number, h: number, r: { left: number; top: number; width: number; height: number }) => p.x < r.left + r.width && p.x + w > r.left && p.y < r.top + r.height && p.y + h > r.top;
  it("beside the node and its neighbours, on the right when there is room", async () => {
    const { placeAway } = await import("../src/lib/graph_focus");
    const node = { left: 300, top: 350, width: 40, height: 50 };
    const nbrs = [{ left: 440, top: 360, width: 40, height: 50 }, { left: 360, top: 470, width: 60, height: 60 }];
    const p = placeAway([node, ...nbrs], 320, 230, area);
    for (const r of [node, ...nbrs]) expect(hits(p, 320, 230, r)).toBe(false);
    expect(p.x).toBeGreaterThanOrEqual(480);
  });
  it("flips to the left at the right edge of the window", async () => {
    const { placeAway } = await import("../src/lib/graph_focus");
    const node = { left: 1100, top: 300, width: 40, height: 50 };
    const p = placeAway([node, { left: 1180, top: 320, width: 40, height: 40 }], 320, 230, area);
    expect(p.x + 320).toBeLessThanOrEqual(1100);
    expect(hits(p, 320, 230, node)).toBe(false);
  });
  it("on a phone it goes below or above, never over the node", async () => {
    const { placeAway } = await import("../src/lib/graph_focus");
    const phone = { left: 0, top: 0, width: 400, height: 860 };
    const node = { left: 140, top: 320, width: 30, height: 40 };
    const p = placeAway([node, { left: 210, top: 330, width: 30, height: 30 }, { left: 100, top: 380, width: 40, height: 40 }], 320, 230, phone);
    expect(hits(p, 320, 230, node)).toBe(false);
    expect(p.x).toBeGreaterThanOrEqual(8);
    expect(p.x + 320).toBeLessThanOrEqual(392);
  });
  it("when nothing is free, it may cover a neighbour but not the node", async () => {
    const { placeAway } = await import("../src/lib/graph_focus");
    const small = { left: 0, top: 0, width: 360, height: 300 };
    const node = { left: 160, top: 130, width: 40, height: 40 };
    const p = placeAway([node, { left: 10, top: 10, width: 340, height: 280 }], 150, 100, small);
    expect(hits(p, 150, 100, node)).toBe(false);
  });
});
