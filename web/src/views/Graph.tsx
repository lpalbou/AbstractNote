import React, { useEffect, useMemo, useRef, useState } from "react";
import { AfChip, AfChipButton } from "@abstractframework/ui-kit";
import { useDebounced, useRead } from "../lib/hooks";
import { layoutGraph } from "../lib/graph_layout";
import { navigate, noteAbout, useAt, type Route } from "../lib/nav";
import { thenNowView } from "../lib/shapes";
import type { Day, EntityCard, EntityListItem, Graph, ListOf, ThenNowRaw, TimelineItem } from "../lib/types";
import { capitalize, claimSentence, longDate, predicateWords, shortDate } from "../lib/words";
import { EntityChip, NoteLink, ReadView, Section, Sparkline, openClaim, openRelation, useApp } from "../components/common";
import { ThenNowCard } from "../components/ThenNow";
import { TimeScrubber } from "../components/TimeScrubber";

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

export function GraphView({ route }: { route: Route }) {
  const { api } = useApp();
  const [at] = useAt();
  const focus = useMemo(() => (route.params.get("focus") || "").split(",").filter(Boolean), [route.params]);
  const depth = Math.min(3, Math.max(1, Number(route.params.get("depth") || 1)));
  const selected = route.params.get("entity") || "";
  const day = DAY_RE.test(route.params.get("day") || "") ? route.params.get("day")! : "";

  const go = (patch: Record<string, string | number | null>) => {
    const cur: Record<string, string | number | null> = { focus: focus.join(","), depth, entity: selected, day };
    navigate("graph", { ...cur, ...patch });
  };

  const now = useRead<Graph>(() => api.get<Graph>("note/graph", { focus, depth, limit: 80 }), [api, focus.join(","), depth]);
  const then = useRead<Graph | null>(() => (at ? api.get<Graph>("note/graph", { focus, depth, limit: 80, at }) : Promise.resolve(null)), [api, focus.join(","), depth, at]);

  return (
    <div className="nt-graph">
      <div className="nt-graph__main">
        <FocusBar focus={focus} depth={depth} onFocus={(ids) => go({ focus: ids.join(","), entity: ids[ids.length - 1] || selected })} onDepth={(d) => go({ depth: d })} />
        <div className="nt-graph__canvas">
          <ReadView read={now} what="the graph" empty={(g) => (g.nodes.length ? false : "Nothing is linked yet. Notes you capture fill this in.")}>
            {(g) => <GraphCanvas graph={g} then={at ? then.data : null} selected={selected} focus={focus} onSelect={(id) => go({ entity: id, day: "" })} />}
          </ReadView>
        </div>
        <TimeScrubber onDay={(d) => go({ day: d, entity: "" })} />
      </div>
      <aside className="nt-graph__side" aria-label="Details">
        {day ? <DayPanel date={day} onClose={() => go({ day: "" })} /> : selected ? <EntityPanel id={selected} at={at} onFocus={() => go({ focus: selected, entity: selected })} /> : <p className="nt-muted nt-pad">Pick an entity in the graph to see what you know about it, then move the time slider to see what you knew then.</p>}
      </aside>
    </div>
  );
}

function FocusBar({ focus, depth, onFocus, onDepth }: { focus: string[]; depth: number; onFocus: (ids: string[]) => void; onDepth: (d: number) => void }) {
  const { api } = useApp();
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 200);
  const [hits, setHits] = useState<EntityListItem[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!dq) {
      setHits([]);
      return;
    }
    let live = true;
    api.get<ListOf<EntityListItem>>("note/entities", { q: dq, limit: 8, sort: "frequent" }).then(
      (d) => live && setHits(d.items || []),
      () => live && setHits([]),
    );
    return () => {
      live = false;
    };
  }, [api, dq]);
  useEffect(() => {
    const missing = focus.filter((id) => !names[id]);
    if (!missing.length) return;
    let live = true;
    Promise.all(missing.map((id) => api.get<EntityCard>(`note/entities/${encodeURIComponent(id)}`).then((e) => [id, e.name] as const, () => [id, id] as const))).then((rows) => {
      if (live) setNames((prev) => ({ ...prev, ...Object.fromEntries(rows) }));
    });
    return () => {
      live = false;
    };
  }, [api, focus, names]);
  return (
    <div className="nt-focusbar">
      <div className="nt-search">
        <input className="nt-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Focus on a person, project, idea…" aria-label="Focus on" />
        {hits.length ? (
          <ul className="nt-suggest" role="listbox" aria-label="Matching entities">
            {hits.map((h) => (
              <li key={h.entity_id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={false}
                  onClick={() => {
                    setNames((prev) => ({ ...prev, [h.entity_id]: h.name }));
                    onFocus([...focus.filter((f) => f !== h.entity_id), h.entity_id]);
                    setQ("");
                  }}
                >
                  <span>{h.name}</span>
                  <span className="nt-muted">{h.kind}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="nt-chips">
        {focus.map((id) => (
          <AfChip key={id} size="sm" tone="accent" onRemove={() => onFocus(focus.filter((f) => f !== id))} removeLabel={`Stop focusing on ${names[id] || "this"}`}>
            {names[id] || "…"}
          </AfChip>
        ))}
        {focus.length ? (
          <label className="nt-depth">
            <span>Reach</span>
            <select className="nt-input" value={depth} onChange={(e) => onDepth(Number(e.target.value))}>
              <option value={1}>Direct links</option>
              <option value={2}>Two steps</option>
              <option value={3}>Three steps</option>
            </select>
          </label>
        ) : (
          <span className="nt-muted">Showing everything you know.</span>
        )}
      </div>
    </div>
  );
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(800);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setW(Math.max(280, Math.round(el.clientWidth))));
    ro.observe(el);
    setW(Math.max(280, Math.round(el.clientWidth)));
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function GraphCanvas({ graph, then, selected, focus, onSelect }: { graph: Graph; then: Graph | null; selected: string; focus: string[]; onSelect: (id: string) => void }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const height = Math.max(320, Math.min(620, Math.round(width * 0.62)));
  const pos = useMemo(() => {
    const placed = layoutGraph({ nodes: graph.nodes.map((n) => ({ id: n.id, weight: n.note_count })), edges: graph.edges, width, height, focus });
    return new Map(placed.map((p) => [p.id, p]));
  }, [graph, width, height, focus]);
  const thenNodes = useMemo(() => (then ? new Set(then.nodes.map((n) => n.id)) : null), [then]);
  const thenEdges = useMemo(() => (then ? new Map(then.edges.map((e) => [`${e.source}|${e.target}|${e.predicate}`, e])) : null), [then]);
  const pairs = new Map<string, number>();
  return (
    <div ref={ref} className="nt-canvas">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Graph of ${graph.nodes.length} entities and ${graph.edges.length} links`}>
        <defs>
          <marker id="nt-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" className="nt-arrow" />
          </marker>
        </defs>
        {graph.edges.map((e) => {
          const a = pos.get(e.source);
          const b = pos.get(e.target);
          if (!a || !b) return null;
          const key = `${e.source}|${e.target}|${e.predicate}`;
          const te = thenEdges ? thenEdges.get(key) : e;
          const existed = Boolean(te);
          const active = te ? te.active : false;
          const pairKey = [e.source, e.target].sort().join("|");
          const nth = pairs.get(pairKey) || 0;
          pairs.set(pairKey, nth + 1);
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const len = Math.max(1, Math.hypot(dx, dy));
          const ux = dx / len;
          const uy = dy / len;
          const sx = a.x + ux * a.r;
          const sy = a.y + uy * a.r;
          const tx = b.x - ux * (b.r + 2);
          const ty = b.y - uy * (b.r + 2);
          const off = nth * 14;
          const mx = (sx + tx) / 2 - uy * off;
          const my = (sy + ty) / 2 + ux * off;
          const count = te ? te.count : e.count;
          return (
            <g key={key} className={`nt-edge${existed ? "" : " nt-edge--future"}${existed && !active ? " nt-edge--old" : ""}`}>
              <line x1={sx} y1={sy} x2={tx} y2={ty} markerEnd="url(#nt-arrow)" />
              <line x1={sx} y1={sy} x2={tx} y2={ty} className="nt-edge__hit" onClick={() => openRelation(e.source, e.target)}>
                <title>{`${predicateWords(e.predicate)}${count > 1 ? ` · ${count} times` : ""} · since ${shortDate(e.first_seen)} — open how this link changed`}</title>
              </line>
              <text x={mx} y={my - 4} textAnchor="middle" className="nt-edge__label" onClick={() => openRelation(e.source, e.target)}>
                {predicateWords(e.predicate)}
                {count > 1 ? ` ×${count}` : ""}
              </text>
            </g>
          );
        })}
        {graph.nodes.map((n) => {
          const p = pos.get(n.id);
          if (!p) return null;
          const existed = !thenNodes || thenNodes.has(n.id);
          return (
            <g
              key={n.id}
              className={`nt-node nt-node--${n.kind}${existed ? "" : " nt-node--future"}${selected === n.id ? " nt-node--selected" : ""}${focus.includes(n.id) ? " nt-node--focus" : ""}`}
              transform={`translate(${p.x},${p.y})`}
              tabIndex={0}
              role="button"
              aria-label={`${n.name}, ${n.kind}${existed ? "" : ", not known yet at that time"}`}
              onClick={() => onSelect(n.id)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === " ") {
                  ev.preventDefault();
                  onSelect(n.id);
                }
              }}
            >
              <circle r={p.r} />
              <text y={p.r + 14} textAnchor="middle">
                {n.name}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="nt-legend">
        <span className="nt-legend__item">
          <svg width="26" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="26" y2="4" className="nt-legend__solid" />
          </svg>
          believed
        </span>
        <span className="nt-legend__item">
          <svg width="26" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="26" y2="4" className="nt-legend__dashed" />
          </svg>
          no longer believed
        </span>
        {then ? <span className="nt-legend__item nt-legend__item--faded">faded: not known yet</span> : null}
      </p>
    </div>
  );
}

export function EntityPanel({ id, at, onFocus }: { id: string; at: string | null; onFocus?: () => void }) {
  const { api } = useApp();
  const card = useRead<EntityCard>(() => api.get<EntityCard>(`note/entities/${encodeURIComponent(id)}`, { at }), [api, id, at]);
  const timeline = useRead<{ items: TimelineItem[] }>(() => api.get<{ items: TimelineItem[] }>(`note/entities/${encodeURIComponent(id)}/timeline`), [api, id]);
  const thenNow = useRead<ThenNowRaw>(() => api.get<ThenNowRaw>(`note/entities/${encodeURIComponent(id)}/then-now`), [api, id]);
  const tn = useMemo(() => thenNowView(thenNow.data), [thenNow.data]);
  const noteDates = useMemo(() => (timeline.data?.items || []).filter((i) => i.type === "note").map((i) => i.at), [timeline.data]);
  const range = useMemo(() => {
    const ts = noteDates.map((d) => Date.parse(d)).filter(Number.isFinite);
    const to = Date.now();
    const from = Math.min(to - 90 * 86400_000, ...ts);
    return { from, to };
  }, [noteDates]);
  return (
    <div className="nt-panel">
      <ReadView read={card} what="this entity">
        {(e) => (
          <>
            <header className="nt-panel__head">
              <div>
                <h2>{e.name}</h2>
                <p className="nt-meta">
                  {capitalize(e.kind)}
                  {e.aliases?.length ? ` · also ${e.aliases.join(", ")}` : ""}
                  {at ? ` · as of ${longDate(at)}` : ""}
                </p>
              </div>
              {timeline.data ? <Sparkline dates={noteDates} from={range.from} to={range.to} label={`${e.name} activity`} /> : null}
            </header>
            {e.summary ? <p>{e.summary}</p> : null}
            <p className="nt-muted">
              {e.note_count ?? 0} {e.note_count === 1 ? "note" : "notes"}
              {e.first_seen ? ` · first ${shortDate(e.first_seen)}` : ""}
              {e.last_seen ? ` · latest ${shortDate(e.last_seen)}` : ""}
            </p>
            {(e.beliefs || []).length ? (
              <Section title={at ? "What you believed then" : "What you believe"}>
                <ul className="nt-list">
                  {(e.beliefs || []).map((c) => (
                    <li key={c.claim_id} className="nt-list__row">
                      <span>{claimSentence(c)}</span>
                      <button type="button" className="nt-btn nt-btn--quiet" onClick={() => openClaim(c.claim_id)}>
                        Why
                      </button>
                    </li>
                  ))}
                </ul>
              </Section>
            ) : null}
            {(e.relations || []).length ? (
              <Section title="Links">
                <ul className="nt-list">
                  {(e.relations || []).map((r) => (
                    <li key={`${r.other_id}|${r.predicate}|${r.direction}`} className="nt-list__row">
                      <button type="button" className={`nt-link${r.active ? "" : " nt-struck"}`} onClick={() => openRelation(e.entity_id, r.other_id)}>
                        <span className="nt-link__title">{r.direction === "out" ? `${predicateWords(r.predicate)} ${r.other_name}` : `${r.other_name} ${predicateWords(r.predicate)} ${e.name}`}</span>
                        <span className="nt-link__date">{r.count > 1 ? `${r.count} times` : shortDate(r.first_seen)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </Section>
            ) : null}
            {(e.open_tasks || []).length ? (
              <Section title="Open loops">
                <ul className="nt-list">
                  {(e.open_tasks || []).map((t) => (
                    <li key={t.task_id}>{t.text}</li>
                  ))}
                </ul>
              </Section>
            ) : null}
            {tn && tn.turning.length ? (
              <Section title="Then and now">
                <ThenNowCard data={tn} />
              </Section>
            ) : null}
            {(e.notes || []).length ? (
              <Section title="Notes">
                {(e.notes || []).slice(0, 8).map((n) => (
                  <NoteLink key={n.note_id} note={n} />
                ))}
              </Section>
            ) : null}
            {onFocus ? (
              <div className="nt-actions">
                <button type="button" className="nt-btn" onClick={onFocus}>
                  Focus here
                </button>
                <button type="button" className="nt-btn" onClick={() => noteAbout("entity", e.entity_id, e.name)}>
                  Add a note
                </button>
              </div>
            ) : null}
          </>
        )}
      </ReadView>
    </div>
  );
}

function DayPanel({ date, onClose }: { date: string; onClose: () => void }) {
  const { api } = useApp();
  const tz = (() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch {
      return "";
    }
  })();
  const day = useRead<Day>(() => api.get<Day>(`note/day/${date}`, { tz }), [api, date]);
  return (
    <div className="nt-panel">
      <header className="nt-panel__head">
        <div>
          <h2>{longDate(`${date}T12:00:00Z`)}</h2>
          <p className="nt-meta">What happened that day</p>
        </div>
      </header>
      <ReadView read={day} what="that day" empty={(d) => (d.notes.length || d.revisions.length || d.entities_touched.length ? false : "Nothing was noted that day.")}>
        {(d) => (
          <>
            {d.notes.length ? (
              <Section title="Notes">
                {d.notes.map((n) => (
                  <NoteLink key={n.note_id} note={n} showDate={false} />
                ))}
              </Section>
            ) : null}
            {d.revisions.length ? (
              <Section title="What changed">
                <ul className="nt-list">
                  {d.revisions.map((r) => (
                    <li key={r.revision_id}>{capitalize(r.summary)}</li>
                  ))}
                </ul>
              </Section>
            ) : null}
            {d.entities_touched.length ? (
              <Section title="Touched">
                <div className="nt-chips">
                  {d.entities_touched.map((e) => (
                    <EntityChip key={e.entity_id} entity={e} />
                  ))}
                </div>
              </Section>
            ) : null}
            {d.claims_superseded.length ? (
              <Section title="No longer believed">
                <ul className="nt-list">
                  {d.claims_superseded.map((c) => (
                    <li key={c.claim_id} className="nt-list__row">
                      <span className="nt-struck">{claimSentence(c)}</span>
                      <button type="button" className="nt-btn nt-btn--quiet" onClick={() => openClaim(c.claim_id)}>
                        Why
                      </button>
                    </li>
                  ))}
                </ul>
              </Section>
            ) : null}
          </>
        )}
      </ReadView>
      <div className="nt-actions">
        <AfChipButton size="md" onClick={onClose}>
          Close the day
        </AfChipButton>
      </div>
    </div>
  );
}
