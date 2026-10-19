import { useMemo } from "react";
import { useRead } from "../lib/hooks";
import { useAt } from "../lib/nav";
import { isoToScrub, scrubToIso } from "../lib/sparkline";
import type { TimelineBuckets } from "../lib/types";
import { longDate } from "../lib/words";
import { useApp } from "./common";

const STEPS = 1000;

/** The calendar day of a moment in this browser's time zone ("2026-08-20"). */
export function localDay(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/**
 * The time slider shared by Explore and Remember: the notebook as it was at a moment (right end = now).
 * A date field beside it reaches any day without dragging (keyboard and assistive technology).
 */
export function TimeScrubber({ onDay }: { onDay?: (date: string) => void }) {
  const { api } = useApp();
  const [at, setAt] = useAt();
  const buckets = useRead<TimelineBuckets>(() => api.get<TimelineBuckets>("note/timeline", { granularity: "week" }), [api]);
  const range = useMemo(() => {
    const to = Date.now();
    const starts = (buckets.data?.buckets || []).map((b) => Date.parse(`${b.bucket}T00:00:00Z`)).filter(Number.isFinite);
    const from = starts.length ? Math.min(...starts) : to - 90 * 86400_000;
    return { from, to };
  }, [buckets.data]);
  const max = Math.max(1, ...(buckets.data?.buckets || []).map((b) => b.notes));
  const value = Math.round(isoToScrub(at, range.from, range.to) * STEPS);
  const label = at ? longDate(at) : "Now";
  const today = localDay(new Date().toISOString());
  const dayOf = localDay(at || new Date().toISOString());
  return (
    <div className="nt-scrubber">
      <div className="nt-scrubber__bars" aria-hidden="true">
        {(buckets.data?.buckets || []).map((b) => {
          const start = `${b.bucket}T00:00:00Z`;
          const left = isoToScrub(start, range.from, range.to) * 100;
          const past = !at || Date.parse(start) <= Date.parse(at);
          return <span key={b.bucket} className={`nt-scrubber__bar${past ? "" : " nt-scrubber__bar--future"}`} style={{ left: `${left}%`, height: `${Math.max(8, (b.notes / max) * 100)}%` }} />;
        })}
      </div>
      <input
        type="range"
        min={0}
        max={STEPS}
        value={value}
        aria-label="Show the notebook as it was on"
        aria-valuetext={label}
        onChange={(e) => setAt(scrubToIso(Number(e.target.value) / STEPS, range.from, range.to))}
      />
      <div className="nt-scrubber__row">
        <span className="nt-scrubber__label" aria-live="polite">
          {at ? `As it was on ${label}` : "As it is now"}
        </span>
        <span className="nt-scrubber__actions">
          <label className="nt-field nt-field--inline">
            <span className="nt-sr">Show the notebook as it was on the date</span>
            <input
              className="nt-input nt-scrubber__date"
              type="date"
              value={at ? localDay(at) : ""}
              max={today}
              onChange={(e) => {
                const v = e.target.value;
                // The end of the chosen day: everything noted that day is included.
                setAt(/^\d{4}-\d{2}-\d{2}$/.test(v) ? (v >= today ? null : new Date(`${v}T23:59:59`).toISOString()) : null);
              }}
            />
          </label>
          {onDay ? (
            <button type="button" className="nt-btn nt-btn--quiet" onClick={() => onDay(dayOf)}>
              That day
            </button>
          ) : null}
          {at ? (
            <button type="button" className="nt-btn nt-btn--quiet" onClick={() => setAt(null)}>
              Back to now
            </button>
          ) : null}
        </span>
      </div>
    </div>
  );
}
