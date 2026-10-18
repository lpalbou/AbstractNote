/** Activity sparkline geometry: counts per bucket between two dates, mapped to an SVG path. */

export type SparkOptions = { from: number; to: number; buckets: number; width: number; height: number; pad?: number };
export type Spark = { counts: number[]; max: number; points: Array<[number, number]>; path: string; area: string };

/** Count dates into `buckets` equal slices of [from, to]; dates outside are ignored. */
export function bucketCounts(times: number[], from: number, to: number, buckets: number): number[] {
  const counts = new Array(Math.max(1, buckets)).fill(0);
  const span = to - from;
  if (!(span > 0)) return counts;
  for (const t of times) {
    if (!Number.isFinite(t) || t < from || t > to) continue;
    const i = Math.min(counts.length - 1, Math.floor(((t - from) / span) * counts.length));
    counts[i] += 1;
  }
  return counts;
}

export function sparkline(isoDates: string[], o: SparkOptions): Spark {
  const pad = o.pad ?? 1.5;
  const counts = bucketCounts(isoDates.map((d) => Date.parse(d)), o.from, o.to, o.buckets);
  const max = Math.max(0, ...counts);
  const n = counts.length;
  const w = o.width - pad * 2;
  const h = o.height - pad * 2;
  const points = counts.map((c, i): [number, number] => {
    const x = pad + (n === 1 ? w / 2 : (i / (n - 1)) * w);
    const y = pad + h - (max ? (c / max) * h : 0);
    return [round(x), round(y)];
  });
  const path = points.map(([x, y], i) => `${i ? "L" : "M"}${x},${y}`).join(" ");
  const base = round(pad + h);
  const area = points.length ? `${path} L${points[points.length - 1][0]},${base} L${points[0][0]},${base} Z` : "";
  return { counts, max, points, path, area };
}

/** The scrubber: a position 0..1 on [from, to] <-> an ISO moment (null at the right end = now). */
export function scrubToIso(fraction: number, from: number, to: number): string | null {
  const f = Math.min(1, Math.max(0, fraction));
  if (f >= 0.999) return null;
  return new Date(from + f * (to - from)).toISOString().replace(/\.\d{3}Z$/, "Z");
}
export function isoToScrub(iso: string | null, from: number, to: number): number {
  if (!iso) return 1;
  const t = Date.parse(iso);
  if (!Number.isFinite(t) || to <= from) return 1;
  return Math.min(1, Math.max(0, (t - from) / (to - from)));
}

function round(v: number): number {
  return Math.round(v * 100) / 100;
}
