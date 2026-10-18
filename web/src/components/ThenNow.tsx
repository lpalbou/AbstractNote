
import type { ThenNow } from "../lib/types";
import { capitalize, longDate, shortDate } from "../lib/words";
import { openNote } from "./common";

/** The earliest and the latest understanding side by side, and the turning points between them. */
export function ThenNowCard({ data, title }: { data: ThenNow; title?: string }) {
  return (
    <div className="nt-thennow">
      {title ? <h3>{title}</h3> : null}
      <div className="nt-thennow__pair">
        <div className="nt-thennow__side">
          <span className="nt-thennow__label">Then · {longDate(data.earliest?.at)}</span>
          <p>{data.earliest?.summary || "Nothing yet."}</p>
        </div>
        <div className="nt-thennow__side nt-thennow__side--now">
          <span className="nt-thennow__label">Now · {longDate(data.latest?.at)}</span>
          <p>{data.latest?.summary || "Nothing yet."}</p>
        </div>
      </div>
      {data.turning_points.length ? (
        <ol className="nt-timeline">
          {data.turning_points.map((t, i) => (
            <li key={`${t.at}:${i}`} className="nt-timeline__item">
              <span className="nt-timeline__date">{shortDate(t.at)}</span>
              <span>{capitalize(t.summary)}</span>
              {t.note_id ? (
                <button type="button" className="nt-btn nt-btn--quiet" onClick={() => openNote(t.note_id!)}>
                  {t.note_title ? `“${t.note_title}”` : "The note"}
                </button>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
