/**
 * The simple line with inflection points (deck slides 04, 19, 20), as geometry: the Overview's activity line
 * (notes per week, markers on the weeks with unusually many new entries or replaced beliefs) and an entity's
 * progression (what was stated about it, period by period, markers where a belief was replaced). Pure and
 * deterministic, so it is tested; the views only draw what these return.
 */

import type { WeekActivity } from "./types";

export type Box = { width: number; height: number; left: number; right: number; top: number; bottom: number };
export type LinePoint = { x: number; y: number; value: number };

const round = (v: number) => Math.round(v * 10) / 10;

/** Points of a line over equally spaced values, the largest at the top of the plot, zero on its baseline. */
export function linePoints(values: number[], box: Box, max = Math.max(0, ...values)): LinePoint[] {
  const w = box.width - box.left - box.right;
  const h = box.height - box.top - box.bottom;
  const n = values.length;
  return values.map((value, i) => ({
    x: round(box.left + (n === 1 ? w / 2 : (i / (n - 1)) * w)),
    y: round(box.top + h - (max > 0 ? (Math.max(0, value) / max) * h : 0)),
    value,
  }));
}

export function linePath(points: Array<{ x: number; y: number }>): string {
  return points.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ");
}

/** The area under a line down to the baseline `base` (a quiet fill behind the line). */
export function areaPath(points: Array<{ x: number; y: number }>, base: number): string {
  if (!points.length) return "";
  return `${linePath(points)} L${points[points.length - 1].x},${base} L${points[0].x},${base} Z`;
}

/** A rounded maximum for the scale (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8 × 10ⁿ), so the top gridline carries a plain
 * number and the line still fills most of the height. */
export function niceMax(v: number): number {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (v <= m * p + 1e-9) return Math.max(1, Math.round(m * p * 100) / 100);
  return 10 * p;
}

/** Keep the axis ticks at least `gap` px apart (the first wins), so month names never run together. */
export function thinTicks<T extends { x: number }>(ticks: T[], gap: number): T[] {
  const out: T[] = [];
  for (const t of ticks) if (!out.length || t.x - out[out.length - 1].x >= gap) out.push(t);
  return out;
}

export type Inflection = { index: number; newEntities: boolean; replaced: boolean; score: number };

const meanSd = (xs: number[]) => {
  const m = xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length);
  const sd = Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / Math.max(1, xs.length));
  return { m, sd };
};

/**
 * The weeks worth a marker: unusually many new entities (at least the mean plus one standard deviation, and at
 * least `minNew`) or beliefs replaced (likewise, at least `minReplaced`). At most `limit`, the strongest kept,
 * returned in time order. A count rule over numbers, never a judgement of meaning.
 */
export function inflections(weeks: Array<Pick<WeekActivity, "new_entities" | "claims_superseded">>, limit = 6, minNew = 2, minReplaced = 1): Inflection[] {
  const e = meanSd(weeks.map((w) => w.new_entities));
  const s = meanSd(weeks.map((w) => w.claims_superseded));
  const te = Math.max(minNew, e.m + e.sd);
  const ts = Math.max(minReplaced, s.m + s.sd);
  const out: Inflection[] = [];
  weeks.forEach((w, index) => {
    const newEntities = w.new_entities >= te;
    const replaced = w.claims_superseded >= ts;
    if (newEntities || replaced) out.push({ index, newEntities, replaced, score: Math.max(newEntities ? w.new_entities / te : 0, replaced ? w.claims_superseded / ts : 0) });
  });
  return out
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, limit)
    .sort((a, b) => a.index - b.index);
}

/**
 * Rows for labels over markers at `xs` (sorted): a label goes on the first row where it is at least `gap` px from
 * the previous label of that row; -1 when no row has room (the tooltip and the table still carry it).
 */
export function labelRows(xs: number[], gap: number, rows = 2): number[] {
  const last: number[] = new Array(rows).fill(-Infinity);
  return xs.map((x) => {
    for (let r = 0; r < rows; r++) {
      if (x - last[r] >= gap) {
        last[r] = x;
        return r;
      }
    }
    return -1;
  });
}

/** Index of the point nearest to x (the crosshair snaps to it). */
export function nearestIndex(points: Array<{ x: number }>, x: number): number {
  let best = 0;
  for (let i = 1; i < points.length; i++) if (Math.abs(points[i].x - x) < Math.abs(points[best].x - x)) best = i;
  return best;
}

/** Running totals. */
export function cumulative(values: number[]): number[] {
  let t = 0;
  return values.map((v) => (t += v));
}

export type Period = { start: number; end: number; label: string };
const DAY = 86400_000;

/** The Monday (UTC) of the week of `t`. */
export function weekStart(t: number): number {
  const d = new Date(t);
  const day = (d.getUTCDay() + 6) % 7;
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - day);
}

/**
 * Periods covering [from, to]: weeks when the span is at most `maxWeeks` weeks, else calendar months (UTC).
 * Each period is [start, end) in ms.
 */
export function periods(from: number, to: number, maxWeeks = 26): Period[] {
  const out: Period[] = [];
  if (!(to >= from)) return out;
  if ((to - from) / (7 * DAY) <= maxWeeks) {
    for (let s = weekStart(from); s <= to; s += 7 * DAY) out.push({ start: s, end: s + 7 * DAY, label: "week" });
    return out;
  }
  const d = new Date(from);
  let y = d.getUTCFullYear();
  let m = d.getUTCMonth();
  for (;;) {
    const s = Date.UTC(y, m, 1);
    if (s > to) break;
    m += 1;
    if (m === 12) {
      m = 0;
      y += 1;
    }
    out.push({ start: s, end: Date.UTC(y, m, 1), label: "month" });
  }
  return out;
}

/** How many of `times` fall in each period. */
export function countIn(times: number[], ps: Period[]): number[] {
  return ps.map((p) => times.filter((t) => t >= p.start && t < p.end).length);
}

export type Progression = { periods: Period[]; added: number[]; total: number[]; replaced: number[]; changes: Array<{ index: number; texts: string[] }> };

/**
 * An entity's progression: statements about it per period (added), their running total (the line), and the
 * periods where a belief was replaced (the markers), each with the words of what changed.
 */
export function progression(statements: string[], turns: Array<{ at: string; text: string }>, now = Date.now()): Progression {
  const times = statements.map((s) => Date.parse(s)).filter(Number.isFinite);
  const turnTimes = turns.map((t) => ({ t: Date.parse(t.at), text: t.text })).filter((t) => Number.isFinite(t.t));
  if (!times.length) return { periods: [], added: [], total: [], replaced: [], changes: [] };
  const from = Math.min(...times, ...turnTimes.map((t) => t.t));
  const to = Math.max(now, ...times);
  const ps = periods(from, to);
  const added = countIn(times, ps);
  const replaced = countIn(turnTimes.map((t) => t.t), ps);
  const changes = ps
    .map((p, index) => ({ index, texts: turnTimes.filter((t) => t.t >= p.start && t.t < p.end).map((t) => t.text) }))
    .filter((c) => c.texts.length);
  return { periods: ps, added, total: cumulative(added), replaced, changes };
}
