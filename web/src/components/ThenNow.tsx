import type { ThenNowView } from "../lib/shapes";
import { capitalize, longDate, shortDate } from "../lib/words";
import { openClaim, openNote } from "./common";

/** The earliest and the latest understanding side by side, and the turning points between them. */
export function ThenNowCard({ data, title }: { data: ThenNowView; title?: string }) {
  const sideView = (label: string, s: ThenNowView["earliest"], now: boolean) => (
    <div className={`nt-thennow__side${now ? " nt-thennow__side--now" : ""}`}>
      <span className="nt-thennow__label">{s?.at ? `${label} · ${longDate(s.at)}` : label}</span>
      {s ? s.lines.map((l, i) => <p key={i}>{capitalize(l)}</p>) : <p className="nt-muted">Nothing yet.</p>}
    </div>
  );
  return (
    <div className="nt-thennow">
      {title ? <h3>{title}</h3> : null}
      <div className="nt-thennow__pair">
        {sideView("Then", data.earliest, false)}
        {sideView("Now", data.latest, true)}
      </div>
      {data.turning.length ? (
        <ol className="nt-timeline">
          {data.turning.map((t, i) => (
            <li key={`${t.at}:${i}`} className="nt-timeline__item">
              {t.at ? <span className="nt-timeline__date">{shortDate(t.at)}</span> : null}
              <span>{capitalize(t.text)}</span>
              {t.claim_id ? (
                <button type="button" className="nt-btn nt-btn--quiet" onClick={() => openClaim(t.claim_id!)}>
                  Why
                </button>
              ) : t.note_id ? (
                <button type="button" className="nt-btn nt-btn--quiet" onClick={() => openNote(t.note_id!)}>
                  The note
                </button>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
