import { useMemo, useState } from "react";
import { useRead, type ReadState } from "../lib/hooks";
import { communitySlots, themeName } from "../lib/graph_focus";
import { inflections } from "../lib/progression";
import type { Communities, Community, NoteStats } from "../lib/types";
import { capitalize, longDate, shortDate } from "../lib/words";
import { ReadView, Section, useApp } from "../components/common";
import { ProgressLine, type LineMarker } from "../components/ProgressLine";

const sum = (o: Record<string, number> | null | undefined) => Object.values(o || {}).reduce((a, b) => a + b, 0);
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const KIND_WORDS: Record<string, string> = { person: "People", project: "Projects", concept: "Ideas", place: "Places", event: "Events", software: "Software", artifact: "Documents", organization: "Organizations", topic: "Topics" };
export const kindWords = (k: string) => KIND_WORDS[k] || capitalize(k);

/** "Week of 14 Sep" from the Monday "2026-09-14". */
export function weekWords(week: string): string {
  return `Week of ${shortDate(`${week}T12:00:00Z`)}`;
}
/** The x-axis ticks of a weekly line: the first week of each month, by its month name. */
export function monthTicks(weeks: string[]): Array<{ index: number; text: string }> {
  const out: Array<{ index: number; text: string }> = [];
  let last = "";
  weeks.forEach((w, index) => {
    const m = w.slice(0, 7);
    if (m === last) return;
    last = m;
    out.push({ index, text: new Date(`${w}T12:00:00Z`).toLocaleDateString(undefined, { month: "short" }) });
  });
  return out;
}
/** The words above a marker: what made the week stand out. */
export function markerWords(w: { new_entities: number; claims_superseded: number }, m: { newEntities: boolean; replaced: boolean }): string {
  const parts: string[] = [];
  if (m.newEntities) parts.push(`${w.new_entities} new`);
  if (m.replaced) parts.push(`${w.claims_superseded} changed`);
  return parts.join(" · ");
}

/**
 * Explore's landing when nothing is focused: what the notebook has accumulated. Headline numbers, the activity
 * line with its turning weeks, entries by kind, the most noted, the themes (a click shows one in the picture),
 * what went quiet, and how processing stands.
 */
export function OverviewView({ communities, onTheme, onEntity }: { communities: ReadState<Communities>; onTheme: (id: string) => void; onEntity: (id: string) => void }) {
  const { api } = useApp();
  const stats = useRead<NoteStats>(() => api.get<NoteStats>("note/stats"), [api]);
  return (
    <div className="nt-overview">
      <ReadView read={stats} what="the overview">
        {(s) => <Accumulated stats={s} communities={communities} onTheme={onTheme} onEntity={onEntity} />}
      </ReadView>
    </div>
  );
}

function Accumulated({ stats, communities, onTheme, onEntity }: { stats: NoteStats; communities: ReadState<Communities>; onTheme: (id: string) => void; onEntity: (id: string) => void }) {
  const weeks = stats.activity_weekly || [];
  const entities = sum(stats.entities_by_kind);
  const beliefs = (stats.claims_by_predicate || []).reduce((a, p) => a + p.count, 0);
  const replaced = weeks.reduce((a, w) => a + w.claims_superseded, 0);
  const p = stats.processing;
  const [table, setTable] = useState(false);
  const marks = useMemo(() => inflections(weeks), [weeks]);
  const markers: LineMarker[] = marks.map((m) => ({ index: m.index, kind: m.newEntities && m.replaced ? "both" : m.newEntities ? "new" : "changed", label: markerWords(weeks[m.index], m) }));
  return (
    <>
      <header className="nt-overview__head">
        <h1>What your notebook holds</h1>
        <p className="nt-muted">{weeks.length ? `Since ${longDate(`${weeks[0].week}T12:00:00Z`)}, week by week.` : "Nothing noted yet."}</p>
      </header>
      <div className="nt-tiles" role="list">
        <Tile label="Notes understood" value={p.understood} sub={p.queued || p.failed ? [p.queued ? `${p.queued} waiting` : "", p.failed ? `${p.failed} ${p.failed === 1 ? "needs" : "need"} attention` : ""].filter(Boolean).join(" · ") : "all up to date"} />
        <Tile label="People, projects and ideas" value={entities} sub={plural(Object.keys(stats.entities_by_kind || {}).length, "kind", "kinds")} />
        <Tile label="Beliefs" value={beliefs} sub={`${replaced} replaced in ${weeks.length} weeks`} />
        <Tile label="Open actions" value={stats.tasks.open} sub={`${stats.tasks.closed} done`} />
        <Tile label="Themes" value={stats.communities} sub="groups your notes form" />
      </div>

      <Section title="How it grew" sub="notes per week" actions={<button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" aria-expanded={table} onClick={() => setTable((v) => !v)}>{table ? "Hide the numbers" : "Show the numbers"}</button>}>
        {weeks.length ? (
          <>
            <ProgressLine
              values={weeks.map((w) => w.notes)}
              ticks={monthTicks(weeks.map((w) => w.week))}
              markers={markers}
              unit="Notes per week"
              label={`Notes per week over ${weeks.length} weeks; ${markers.length} weeks stand out. Use the arrow keys for each week.`}
              tip={(i) => {
                const w = weeks[i];
                return {
                  title: weekWords(w.week),
                  rows: [
                    { value: String(w.notes), label: w.notes === 1 ? "note" : "notes", key: "line" },
                    { value: String(w.new_entities), label: "new people, projects or ideas", key: "new" },
                    { value: String(w.claims_added), label: "beliefs stated" },
                    { value: String(w.claims_superseded), label: "beliefs replaced", key: "changed" },
                  ],
                };
              }}
            />
            <p className="nt-pline__legend">
              <span className="nt-pline__legend-item nt-pline__legend-item--new">many new people, projects or ideas</span>
              <span className="nt-pline__legend-item nt-pline__legend-item--changed">beliefs replaced</span>
            </p>
            {table ? <WeekTable weeks={weeks} /> : null}
          </>
        ) : (
          <p className="nt-muted">Nothing noted yet.</p>
        )}
      </Section>

      <div className="nt-overview__pair">
        <Section title="By kind">
          <Bars rows={Object.entries(stats.entities_by_kind || {}).map(([k, n]) => ({ key: k, label: kindWords(k), value: n }))} unit={(n) => plural(n, "entry", "entries")} />
        </Section>
        <Section title="Most noted">
          <Bars rows={(stats.top_entities || []).map((e) => ({ key: e.entity_id, label: e.name, value: e.note_count, hint: `${e.kind} · latest ${shortDate(e.last_seen)}`, onClick: () => onEntity(e.entity_id) }))} unit={(n) => plural(n, "note", "notes")} keepOrder />
        </Section>
      </div>

      <Section title="Themes" sub="people, projects and ideas that come up together; choose one to see it in the picture">
        <ThemeCards communities={communities} onTheme={onTheme} />
      </Section>

      <div className="nt-overview__pair">
        <Section title="Gone quiet" sub="not in a note for three weeks or more">
          {(stats.quiet_entities || []).length ? (
            <ul className="nt-list nt-quiet">
              {stats.quiet_entities.map((q) => (
                <li key={q.entity_id} className="nt-list__row">
                  <button type="button" className="nt-link" onClick={() => onEntity(q.entity_id)}>
                    <span className="nt-link__title">{q.name}</span>
                  </button>
                  <span className="nt-link__date">{`last noted ${shortDate(q.last_seen)}`}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="nt-muted">Everything came up recently.</p>
          )}
        </Section>
        <Section title="Processing">
          <Processing p={p} />
        </Section>
      </div>
    </>
  );
}

function Tile({ label, value, sub }: { label: string; value: number; sub: string }) {
  return (
    <div className="nt-tile" role="listitem">
      <span className="nt-tile__label">{label}</span>
      <span className="nt-tile__value">{value.toLocaleString()}</span>
      <span className="nt-tile__sub">{sub}</span>
    </div>
  );
}

/** Horizontal bars, one hue, the value in words beside each bar (no hover needed to read it). */
function Bars({ rows, unit, keepOrder }: { rows: Array<{ key: string; label: string; value: number; hint?: string; onClick?: () => void }>; unit: (n: number) => string; keepOrder?: boolean }) {
  if (!rows.length) return <p className="nt-muted">Nothing yet.</p>;
  const sorted = keepOrder ? rows : [...rows].sort((a, b) => b.value - a.value || a.label.localeCompare(b.label));
  const max = Math.max(1, ...sorted.map((r) => r.value));
  return (
    <ul className="nt-bars">
      {sorted.map((r) => (
        <li key={r.key} className="nt-bars__row" title={`${r.label}: ${unit(r.value)}${r.hint ? ` · ${r.hint}` : ""}`}>
          {r.onClick ? (
            <button type="button" className="nt-link nt-bars__label" onClick={r.onClick}>
              <span className="nt-link__title">{r.label}</span>
            </button>
          ) : (
            <span className="nt-bars__label">{r.label}</span>
          )}
          <span className="nt-bars__track" aria-hidden="true">
            <span className="nt-bars__bar" style={{ width: `${Math.max(2, (r.value / max) * 100)}%` }} />
          </span>
          <span className="nt-bars__value">{r.value}</span>
        </li>
      ))}
    </ul>
  );
}

function WeekTable({ weeks }: { weeks: NoteStats["activity_weekly"] }) {
  return (
    <div className="nt-rels__wrap">
      <table className="nt-rels nt-weeks">
        <caption>Each week: notes, new people, projects or ideas, beliefs stated and replaced</caption>
        <thead>
          <tr>
            <th scope="col">Week</th>
            <th scope="col">Notes</th>
            <th scope="col">New</th>
            <th scope="col">Stated</th>
            <th scope="col">Replaced</th>
          </tr>
        </thead>
        <tbody>
          {weeks.map((w) => (
            <tr key={w.week}>
              <td>{weekWords(w.week)}</td>
              <td>{w.notes}</td>
              <td>{w.new_entities}</td>
              <td>{w.claims_added}</td>
              <td>{w.claims_superseded}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ThemeCards({ communities, onTheme }: { communities: ReadState<Communities>; onTheme: (id: string) => void }) {
  return (
    <ReadView read={communities} what="the themes" empty={(c) => (c.items.length ? false : "No themes yet: they appear once a few notes connect.")}>
      {(c) => {
        const slots = communitySlots(c.items);
        return (
          <ul className="nt-themecards">
            {[...c.items].sort((a, b) => (slots.get(a.community_id) || 99) - (slots.get(b.community_id) || 99)).map((t) => (
              <li key={t.community_id}>
                <ThemeCard theme={t} slot={slots.get(t.community_id) || 0} onOpen={() => onTheme(t.community_id)} />
              </li>
            ))}
          </ul>
        );
      }}
    </ReadView>
  );
}

function ThemeCard({ theme, slot, onOpen }: { theme: Community; slot: number; onOpen: () => void }) {
  return (
    <button type="button" className={`nt-themecard nt-c${slot}`} onClick={onOpen} aria-label={`${themeName(theme)}: show it in the picture`}>
      <span className="nt-themecard__head">
        <span className="nt-theme__dot" aria-hidden="true" />
        <span className="nt-themecard__label">{themeName(theme)}</span>
        <span className="nt-themecard__size">{plural(theme.size, "entry", "entries")}</span>
      </span>
      {theme.summary ? <span className="nt-themecard__summary">{theme.summary}</span> : null}
      {theme.top_entities.length ? <span className="nt-themecard__top">{theme.top_entities.map((e) => e.name).join(" · ")}</span> : null}
      {theme.stale_label ? <span className="nt-themecard__stale">{`Named on ${shortDate(theme.labelled_at)}; the theme has changed since.`}</span> : null}
    </button>
  );
}

/** How processing stands: three numbers in words, and one bar split in the same three parts. */
function Processing({ p }: { p: NoteStats["processing"] }) {
  const total = Math.max(1, p.understood + p.queued + p.failed);
  const parts = [
    { key: "done", n: p.understood, words: "understood" },
    { key: "waiting", n: p.queued, words: "waiting" },
    { key: "attention", n: p.failed, words: "need attention" },
  ];
  return (
    <div className="nt-processing">
      <div className="nt-processing__bar" aria-hidden="true">
        {parts.filter((x) => x.n > 0).map((x) => (
          <span key={x.key} className={`nt-processing__part nt-processing__part--${x.key}`} style={{ flexGrow: x.n / total }} />
        ))}
      </div>
      <ul className="nt-processing__legend">
        {parts.map((x) => (
          <li key={x.key} className={`nt-processing__item nt-processing__item--${x.key}`}>
            <strong>{x.n}</strong> {x.n === 1 && x.key === "attention" ? "needs attention" : x.words}
          </li>
        ))}
      </ul>
      {p.failed ? <p className="nt-muted">Open Settings to try the notes that need attention again.</p> : null}
    </div>
  );
}
