import { useMemo } from "react";
import { useRead } from "../lib/hooks";
import { useAt } from "../lib/nav";
import { isoToScrub, scrubToIso } from "../lib/sparkline";
import type { TimelineBuckets } from "../lib/types";
import { longDate } from "../lib/words";
import { useApp } from "./common";

const STEPS = 1000;

/** The time slider shared by Graph and Remember: the notebook as it was at a moment (right end = now). */
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
  const dayOf = (at || new Date().toISOString()).slice(0, 10);
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
        <span className="nt-scrubber__label">{at ? `As it was on ${label}` : "As it is now"}</span>
        <span className="nt-scrubber__actions">
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
