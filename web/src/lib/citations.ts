/** Answers cite notes inline as `[nt_...]` (docs/cognition.md "Answer"); show them as numbered links. */

export type Segment = { text: string } | { cite: number; noteId: string };

const CITE = /\[(nt_[a-z2-7]{6,})\]/g;

export function splitCitations(answer: string): { segments: Segment[]; cited: string[] } {
  const segments: Segment[] = [];
  const cited: string[] = [];
  let last = 0;
  for (const m of String(answer || "").matchAll(CITE)) {
    const i = m.index ?? 0;
    if (i > last) segments.push({ text: answer.slice(last, i) });
    let n = cited.indexOf(m[1]);
    if (n < 0) {
      cited.push(m[1]);
      n = cited.length - 1;
    }
    segments.push({ cite: n + 1, noteId: m[1] });
    last = i + m[0].length;
  }
  if (last < answer.length) segments.push({ text: answer.slice(last) });
  return { segments, cited };
}
