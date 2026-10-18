/**
 * Force layout for the Graph view (d3-force, computed synchronously so a graph
 * renders in one pass and the same graph always lands in the same place:
 * deterministic start positions, fixed tick count, no randomness).
 */

import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";

export type LayoutNode = SimulationNodeDatum & { id: string; r: number };
export type LayoutInput = { nodes: Array<{ id: string; weight?: number }>; edges: Array<{ source: string; target: string }>; width: number; height: number; ticks?: number; focus?: string[] };
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
    return { id: n.id, r: nodeRadius(n.weight), x: focus.has(n.id) ? cx : cx + Math.cos(angle) * ring, y: focus.has(n.id) ? cy : cy + Math.sin(angle) * ring };
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
