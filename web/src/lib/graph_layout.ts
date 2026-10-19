/**
 * Force layout for the Graph view (d3-force, computed synchronously so a graph
 * renders in one pass and the same graph always lands in the same place:
 * deterministic start positions, fixed tick count, no randomness).
 */

import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";

export type LayoutNode = SimulationNodeDatum & { id: string; r: number };
export type LayoutInput = { nodes: Array<{ id: string; weight?: number; r?: number }>; edges: Array<{ source: string; target: string }>; width: number; height: number; ticks?: number; focus?: string[] };
export type Positioned = { id: string; x: number; y: number; r: number };

export function nodeRadius(weight = 1): number {
  return Math.round(8 + Math.min(14, Math.sqrt(Math.max(0, weight)) * 3));
}

export function layoutGraph(input: LayoutInput): Positioned[] {
  const { width, height } = input;
  const cx = width / 2;
  const cy = height / 2;
  const ids = input.nodes.map((n) => n.id).sort();
  const order = new Map(ids.map((id, i) => [id, i]));
  const focus = new Set(input.focus || []);
  const nodes: LayoutNode[] = input.nodes.map((n) => {
    const i = order.get(n.id) || 0;
    const angle = (i / Math.max(1, ids.length)) * Math.PI * 2;
    const ring = Math.min(width, height) * 0.32;
    return { id: n.id, r: n.r ?? nodeRadius(n.weight), x: focus.has(n.id) ? cx : cx + Math.cos(angle) * ring, y: focus.has(n.id) ? cy : cy + Math.sin(angle) * ring };
  });
  const known = new Set(nodes.map((n) => n.id));
  const links: Array<SimulationLinkDatum<LayoutNode>> = input.edges.filter((e) => known.has(e.source) && known.has(e.target) && e.source !== e.target).map((e) => ({ source: e.source, target: e.target }));
  const sim = forceSimulation<LayoutNode>(nodes)
    .randomSource(() => 0.5)
    .force("link", forceLink<LayoutNode, SimulationLinkDatum<LayoutNode>>(links).id((d) => d.id).distance(110).strength(0.5))
    .force("charge", forceManyBody<LayoutNode>().strength(-420))
    .force("collide", forceCollide<LayoutNode>().radius((d) => d.r + 26))
    .force("x", forceX<LayoutNode>(cx).strength(0.06))
    .force("y", forceY<LayoutNode>(cy).strength(0.08))
    .force("center", forceCenter(cx, cy))
    .stop();
  const ticks = input.ticks ?? 300;
  for (let i = 0; i < ticks; i++) sim.tick();
  const margin = 40;
  return nodes.map((n) => ({
    id: n.id,
    r: n.r,
    x: Math.round(Math.min(width - margin, Math.max(margin, n.x ?? cx))),
    y: Math.round(Math.min(height - margin, Math.max(margin, n.y ?? cy))),
  }));
}

/* ------------------------------------------------------------------ view: fit, zoom, pan, settle */

/** Screen = world × k + (x, y). */
export type ViewTransform = { k: number; x: number; y: number };
export type Bounds = { minX: number; minY: number; maxX: number; maxY: number };
export const MIN_ZOOM = 0.2;
export const MAX_ZOOM = 4;
/** A fit never enlarges past this (one or two nodes would otherwise fill the screen). */
export const MAX_FIT_ZOOM = 2.2;

/** The world box the nodes and their labels occupy (a label hangs under its circle). */
export function boundsOf(nodes: Array<{ x: number; y: number; r: number }>, label = { below: 22, side: 46 }): Bounds | null {
  if (!nodes.length) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of nodes) {
    minX = Math.min(minX, n.x - Math.max(n.r, label.side));
    maxX = Math.max(maxX, n.x + Math.max(n.r, label.side));
    minY = Math.min(minY, n.y - n.r);
    maxY = Math.max(maxY, n.y + n.r + label.below);
  }
  return { minX, minY, maxX, maxY };
}

/** The transform that shows `b` centred in a `width` × `height` view with `pad` pixels around it. */
export function fitView(b: Bounds | null, width: number, height: number, pad = 28, maxK = MAX_FIT_ZOOM): ViewTransform {
  if (!b || width <= 0 || height <= 0) return { k: 1, x: 0, y: 0 };
  const bw = Math.max(1, b.maxX - b.minX);
  const bh = Math.max(1, b.maxY - b.minY);
  const k = Math.max(MIN_ZOOM, Math.min(maxK, (width - 2 * pad) / bw, (height - 2 * pad) / bh));
  const cx = (b.minX + b.maxX) / 2;
  const cy = (b.minY + b.maxY) / 2;
  return { k, x: width / 2 - cx * k, y: height / 2 - cy * k };
}

/** Zoom by `factor` keeping the world point under the screen point (px, py) where it is. */
export function zoomAt(v: ViewTransform, factor: number, px: number, py: number, minK = MIN_ZOOM, maxK = MAX_ZOOM): ViewTransform {
  const k = Math.max(minK, Math.min(maxK, v.k * factor));
  const wx = (px - v.x) / v.k;
  const wy = (py - v.y) / v.k;
  return { k, x: px - wx * k, y: py - wy * k };
}

/** The world point under a screen point. */
export function toWorld(v: ViewTransform, sx: number, sy: number): { x: number; y: number } {
  return { x: (sx - v.x) / v.k, y: (sy - v.y) / v.k };
}

/**
 * After a node is dropped: the layout settles around it (it stays where it was put). Returns the frames of the
 * settling, `steps` of them, `ticksPerStep` simulation ticks apart, deterministic (no randomness).
 */
export function settleFrames(nodes: Positioned[], edges: Array<{ source: string; target: string }>, fixed: { id: string; x: number; y: number }, steps = 10, ticksPerStep = 12): Positioned[][] {
  const sim: LayoutNode[] = nodes.map((n) => ({ id: n.id, r: n.r, x: n.x, y: n.y, ...(n.id === fixed.id ? { fx: fixed.x, fy: fixed.y } : {}) }));
  const known = new Set(sim.map((n) => n.id));
  const links: Array<SimulationLinkDatum<LayoutNode>> = edges.filter((e) => known.has(e.source) && known.has(e.target) && e.source !== e.target).map((e) => ({ source: e.source, target: e.target }));
  const s = forceSimulation<LayoutNode>(sim)
    .randomSource(() => 0.5)
    .alpha(0.5)
    .force("link", forceLink<LayoutNode, SimulationLinkDatum<LayoutNode>>(links).id((d) => d.id).distance(110).strength(0.5))
    .force("charge", forceManyBody<LayoutNode>().strength(-420))
    .force("collide", forceCollide<LayoutNode>().radius((d) => d.r + 26))
    .stop();
  const frames: Positioned[][] = [];
  for (let i = 0; i < steps; i++) {
    for (let t = 0; t < ticksPerStep; t++) s.tick();
    frames.push(sim.map((n) => ({ id: n.id, r: n.r, x: Math.round(n.x ?? 0), y: Math.round(n.y ?? 0) })));
  }
  return frames;
}
