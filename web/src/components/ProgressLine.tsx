import React, { useEffect, useMemo, useRef, useState } from "react";
import { placeCard } from "../lib/graph_focus";
import { areaPath, labelRows, linePath, linePoints, nearestIndex, niceMax, thinTicks, type Box } from "../lib/progression";

export type LineMarker = { index: number; kind: "new" | "changed" | "both"; label: string };
export type TipRow = { value: string; label: string; key?: "line" | "new" | "changed" };

/**
 * The simple line with inflection points (deck slides 04, 19, 20): one series, thin, a quiet area under it,
 * markers on the moments that matter with a short label above, a baseline and one top gridline. Hover or the
 * arrow keys move a hairline that snaps to the nearest point; the tooltip gives the exact values (value first).
 * The same numbers are in the caller's table, so nothing is reachable only by hovering.
 */
export function ProgressLine({ values, ticks, markers, tip, label, height = 170, unit }: { values: number[]; ticks: Array<{ index: number; text: string }>; markers: LineMarker[]; tip: (index: number) => { title: string; rows: TipRow[] }; label: string; height?: number; unit: string }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [active, setActive] = useState<number | null>(null);
  const [tipBox, setTipBox] = useState<{ x: number; y: number } | null>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const measure = () => setWidth(Math.max(200, Math.round(el.clientWidth)));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const hasLabels = markers.some((m) => m.label);
  const box: Box = { width, height, left: 30, right: 14, top: hasLabels ? 40 : 14, bottom: 24 };
  const max = niceMax(Math.max(0, ...values));
  const pts = useMemo(() => linePoints(values, box, max), [values, width, height, max, hasLabels]); // eslint-disable-line react-hooks/exhaustive-deps
  const base = box.height - box.bottom;
  const sortedMarkers = [...markers].sort((a, b) => a.index - b.index);
  const rows = labelRows(sortedMarkers.map((m) => pts[m.index]?.x ?? 0), Math.min(120, width / 4));

  useEffect(() => {
    if (active === null || !tipRef.current || !wrap.current) return setTipBox(null);
    const p = pts[active];
    const r = tipRef.current.getBoundingClientRect();
    // Beside the point, preferably below the line (it may hang under the chart) so the line stays in sight.
    const c = placeCard(p.x, Math.max(p.y, base - 20), r.width, r.height, { left: 0, top: -8, width, height: height + Math.max(16, r.height) }, 12, 0);
    setTipBox(c);
  }, [active, pts, width, height, base]);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    setActive(nearestIndex(pts, e.clientX - r.left));
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (!values.length) return;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const d = e.key === "ArrowRight" ? 1 : -1;
      setActive((a) => Math.max(0, Math.min(values.length - 1, (a ?? (d > 0 ? -1 : values.length)) + d)));
    }
    if (e.key === "Home") setActive(0);
    if (e.key === "End") setActive(values.length - 1);
    if (e.key === "Escape") setActive(null);
  };
  const t = active !== null ? tip(active) : null;

  return (
    <div ref={wrap} className="nt-pline">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className="nt-pline__svg"
        role="img"
        aria-label={label}
        tabIndex={0}
        onPointerMove={onMove}
        onPointerLeave={() => setActive(null)}
        onFocus={() => setActive((a) => a ?? values.length - 1)}
        onBlur={() => setActive(null)}
        onKeyDown={onKey}
      >
        <line className="nt-pline__grid" x1={box.left} x2={width - box.right} y1={box.top} y2={box.top} />
        <text className="nt-pline__axis" x={box.left - 6} y={box.top + 4} textAnchor="end">
          {max}
        </text>
        <line className="nt-pline__base" x1={box.left} x2={width - box.right} y1={base} y2={base} />
        <text className="nt-pline__axis" x={box.left - 6} y={base + 4} textAnchor="end">
          0
        </text>
        {thinTicks(ticks.filter((tk) => pts[tk.index]).map((tk) => ({ ...tk, x: pts[tk.index].x })), 40).map((tk) => (
          <text key={tk.index} className="nt-pline__axis" x={tk.x} y={height - 6} textAnchor="middle">
            {tk.text}
          </text>
        ))}
        <path className="nt-pline__area" d={areaPath(pts, base)} />
        <path className="nt-pline__line" d={linePath(pts)} />
        {active !== null && pts[active] ? <line className="nt-pline__hair" x1={pts[active].x} x2={pts[active].x} y1={box.top - 4} y2={base} /> : null}
        {sortedMarkers.map((m, i) => {
          const p = pts[m.index];
          if (!p) return null;
          const row = rows[i];
          return (
            <g key={`${m.index}:${m.kind}`} className={`nt-pline__mark nt-pline__mark--${m.kind}`}>
              {m.label && row >= 0 ? (
                <>
                  <line className="nt-pline__stem" x1={p.x} x2={p.x} y1={row === 0 ? 14 : 28} y2={p.y - 7} />
                  <text className="nt-pline__note" x={Math.max(box.left, Math.min(width - box.right, p.x))} y={row === 0 ? 10 : 24} textAnchor={p.x < box.left + 40 ? "start" : p.x > width - box.right - 40 ? "end" : "middle"}>
                    {m.label}
                  </text>
                </>
              ) : null}
              <circle cx={p.x} cy={p.y} r={5} />
            </g>
          );
        })}
        {active !== null && pts[active] ? <circle className="nt-pline__dot" cx={pts[active].x} cy={pts[active].y} r={3.5} /> : null}
      </svg>
      {t ? (
        <div ref={tipRef} className="nt-pline__tip" role="status" style={tipBox ? { left: tipBox.x, top: tipBox.y } : { left: 0, top: 0, visibility: "hidden" }}>
          <p className="nt-pline__tip-title">{t.title}</p>
          <ul>
            {t.rows.map((r) => (
              <li key={r.label} className={r.key ? `nt-pline__key nt-pline__key--${r.key}` : undefined}>
                <strong>{r.value}</strong>
                <span>{r.label}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <span className="nt-sr">{`${unit}: ${values.join(", ")}`}</span>
    </div>
  );
}
