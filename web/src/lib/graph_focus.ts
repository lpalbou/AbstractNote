/**
 * Explore's readability rules (W16), pure so they are tested: what a hover lights up, when an edge shows its
 * words, where the hover card goes, the strongest links in words, node size by note count, and the colour
 * slot of each community.
 */

import { predicateWords } from "./words";

export type FocusEdge = { source: string; target: string; predicate: string; count: number; active: boolean; last_seen?: string; first_seen?: string };
export const edgeKey = (e: { source: string; target: string; predicate: string }) => `${e.source}|${e.target}|${e.predicate}`;

/** What the pointer (or keyboard focus) is on. */
export type Hover = { kind: "node"; id: string } | { kind: "edge"; key: string; source: string; target: string } | null;
/** What stays at full strength while something is hovered; null = nothing hovered, everything as usual. */
export type Lit = { nodes: Set<string>; edges: Set<string> } | null;

/** A hovered node keeps itself, its edges and its neighbours; a hovered edge keeps itself and its two ends. */
export function litBy(hover: Hover, edges: FocusEdge[]): Lit {
  if (!hover) return null;
  if (hover.kind === "edge") return { nodes: new Set([hover.source, hover.target]), edges: new Set([hover.key]) };
  const nodes = new Set([hover.id]);
  const lit = new Set<string>();
  for (const e of edges) {
    if (e.source !== hover.id && e.target !== hover.id) continue;
    nodes.add(e.source);
    nodes.add(e.target);
    lit.add(edgeKey(e));
  }
  return { nodes, edges: lit };
}

/** Edge words show once the picture is zoomed in this far (screen px per layout px), or on hover. */
export const EDGE_LABEL_ZOOM = 1.4;
/** An edge shorter than this on screen (between its two circles) has no room for its words: the card says them. */
export const MIN_LABEL_EDGE_PX = 80;
export function edgeLabelShown(k: number, litEdge: boolean, faded: boolean, screenLength = Infinity): boolean {
  if (screenLength < MIN_LABEL_EDGE_PX) return false;
  if (litEdge) return true;
  return !faded && k >= EDGE_LABEL_ZOOM;
}

/** The hover appears after the pointer rests this long, so a pointer passing over the picture shows nothing. */
export const CARD_DELAY_MS = 300;

/**
 * Where the hover card goes: below-right of the pointer, flipped left or above when it would cross the edge of
 * the visible area, then clamped inside it with a margin (a card larger than the area pins to its top-left).
 */
export function placeCard(px: number, py: number, cw: number, ch: number, area: { left: number; top: number; width: number; height: number }, gap = 14, margin = 8): { x: number; y: number } {
  const right = area.left + area.width - margin;
  const bottom = area.top + area.height - margin;
  let x = px + gap;
  let y = py + gap;
  if (x + cw > right) x = px - gap - cw;
  if (y + ch > bottom) y = py - gap - ch;
  x = Math.max(area.left + margin, Math.min(x, right - cw));
  y = Math.max(area.top + margin, Math.min(y, bottom - ch));
  return { x: Math.round(x), y: Math.round(y) };
}

export type Rect = { left: number; top: number; width: number; height: number };
const overlap = (a: Rect, b: Rect) => Math.max(0, Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left)) * Math.max(0, Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top));
const union = (rs: Rect[]): Rect => {
  const l = Math.min(...rs.map((r) => r.left));
  const t = Math.min(...rs.map((r) => r.top));
  return { left: l, top: t, width: Math.max(...rs.map((r) => r.left + r.width)) - l, height: Math.max(...rs.map((r) => r.top + r.height)) - t };
};

/**
 * Where the hover card goes so it never covers what it describes: beside the box around the hovered node and
 * its neighbours (`avoid`, the node first): right, left, below, above; then the same around the node alone.
 * Each place is kept inside `area` with a margin; the first that covers none of `avoid` wins, else the one
 * covering least of it.
 */
export function placeAway(avoid: Rect[], cw: number, ch: number, area: Rect, gap = 16, margin = 8): { x: number; y: number } {
  if (!avoid.length) return { x: area.left + margin, y: area.top + margin };
  const clampX = (x: number) => Math.max(area.left + margin, Math.min(x, area.left + area.width - margin - cw));
  const clampY = (y: number) => Math.max(area.top + margin, Math.min(y, area.top + area.height - margin - ch));
  const node = avoid[0];
  const cx = node.left + node.width / 2;
  const cy = node.top + node.height / 2;
  const around = (b: Rect) => [
    { x: b.left + b.width + gap, y: cy - ch / 2 },
    { x: b.left - gap - cw, y: cy - ch / 2 },
    { x: cx - cw / 2, y: b.top + b.height + gap },
    { x: cx - cw / 2, y: b.top - gap - ch },
  ];
  let best = { x: 0, y: 0, cost: Infinity };
  for (const c of [...around(union(avoid)), ...around(node)]) {
    const x = clampX(c.x);
    const y = clampY(c.y);
    const box = { left: x, top: y, width: cw, height: ch };
    // Covering the hovered node itself costs most; a neighbour less.
    const cost = avoid.reduce((sum, r, i) => sum + overlap(box, r) * (i === 0 ? 10 : 1), 0);
    if (cost === 0) return { x: Math.round(x), y: Math.round(y) };
    if (cost < best.cost) best = { x, y, cost };
  }
  return { x: Math.round(best.x), y: Math.round(best.y) };
}

/**
 * The two or three strongest links of a node in words, strongest first: believed before no longer believed,
 * then how often it was said, then the most recent. "led by Dana" (out), "Dana leads it" is not invented:
 * an incoming link reads "<other> <predicate> <name>".
 */
export function strongestLinks(id: string, name: string, edges: FocusEdge[], names: Map<string, string>, n = 3): string[] {
  const t = (iso?: string) => (iso ? Date.parse(iso) || 0 : 0);
  return edges
    .filter((e) => (e.source === id || e.target === id) && e.source !== e.target)
    .sort((a, b) => Number(b.active) - Number(a.active) || b.count - a.count || t(b.last_seen) - t(a.last_seen))
    .slice(0, n)
    .map((e) => {
      const words = predicateWords(e.predicate);
      const other = names.get(e.source === id ? e.target : e.source) || "something";
      const line = e.source === id ? `${words} ${other}` : `${other} ${words} ${name}`;
      return `${line}${e.count > 1 ? ` (${e.count} times)` : ""}${e.active ? "" : ", no longer believed"}`;
    });
}

/** The first sentence of a summary, never longer than `max` characters (cut at a word, with an ellipsis). */
export function firstSentence(text: string | null | undefined, max = 160): string {
  const s = String(text || "").trim().replace(/\s+/g, " ");
  if (!s) return "";
  const end = s.search(/[.!?](\s|$)/);
  const one = end >= 0 ? s.slice(0, end + 1) : s;
  if (one.length <= max) return one;
  const cut = one.slice(0, max - 1);
  return `${cut.slice(0, Math.max(cut.lastIndexOf(" "), max / 2)).replace(/[,;:\s]+$/, "")}…`;
}

/** Node radius by note count on a square-root scale (area grows with the count), from `min` to `max` px. */
export function radiusScale(maxCount: number, min = 7, max = 24): (count: number | undefined) => number {
  const top = Math.max(1, maxCount);
  return (count) => Math.round((min + (max - min) * Math.sqrt(Math.max(0, Math.min(top, count || 0)) / top)) * 10) / 10;
}

/** The categorical colour slots (styles.css --nt-c1..--nt-c6); a seventh community and beyond share "other". */
export const COMMUNITY_SLOTS = 6;
/**
 * The colour slot of each community: largest first, then by label, then id, so a community keeps its colour
 * whatever subset of the picture is shown (colour follows the community, never its rank in the view).
 * 0 = "other" (no community, or past the sixth).
 */
export function communitySlots(communities: Array<{ community_id: string; label: string | null; size: number }>): Map<string, number> {
  const order = [...communities].sort((a, b) => b.size - a.size || (a.label || "").localeCompare(b.label || "") || a.community_id.localeCompare(b.community_id));
  return new Map(order.map((c, i) => [c.community_id, i < COMMUNITY_SLOTS ? i + 1 : 0]));
}

/** A community's name for a person: its label, or plain words while the notebook has not named it yet. */
export function themeName(c: { label: string | null } | null | undefined): string {
  return (c && c.label) || "A theme not named yet";
}
