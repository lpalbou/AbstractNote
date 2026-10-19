import React, { useEffect, useMemo, useRef, useState } from "react";
import { AfChip, AfChipButton, AfTabs } from "@abstractframework/ui-kit";
import { ApiError } from "../lib/api";
import { useDebounced, useRead } from "../lib/hooks";
import { layoutGraph } from "../lib/graph_layout";
import { milestones } from "../lib/milestones";
import { navigate, noteAbout, useAt, type Route } from "../lib/nav";
import { thenNowView } from "../lib/shapes";
import type { Day, EntityCard, EntityListItem, Graph, ListOf, ThenNowRaw, TimelineItem } from "../lib/types";
import { capitalize, claimSentence, longDate, predicateWords, shortDate } from "../lib/words";
import { EntityChip, KindBadge, NoteLink, ReadView, Section, Sparkline, openClaim, openNote, openRelation, useApp } from "../components/common";
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
  const asList = route.params.get("view") === "list";

  const go = (patch: Record<string, string | number | null>) => {
    const cur: Record<string, string | number | null> = { focus: focus.join(","), depth, entity: selected, day, view: asList ? "list" : null };
    navigate("graph", { ...cur, ...patch });
  };

  const now = useRead<Graph>(() => api.get<Graph>("note/graph", { focus, depth, limit: 80 }), [api, focus.join(","), depth]);
  const then = useRead<Graph | null>(() => (at ? api.get<Graph>("note/graph", { focus, depth, limit: 80, at }) : Promise.resolve(null)), [api, focus.join(","), depth, at]);

  return (
    <div className="nt-graph">
      <div className="nt-graph__main">
        <FocusBar focus={focus} depth={depth} onFocus={(ids) => go({ focus: ids.join(","), entity: ids[ids.length - 1] || selected })} onDepth={(d) => go({ depth: d })} asList={asList} onList={(v) => go({ view: v ? "list" : null })} />
        <div className="nt-graph__canvas">
          <ReadView read={now} what="the connections" empty={(g) => (g.nodes.length ? false : "Nothing is linked yet. Notes you capture fill this in.")}>
            {(g) =>
              asList ? (
                <RelationList graph={g} then={at ? then.data : null} onSelect={(id) => go({ entity: id, day: "" })} />
              ) : (
                <GraphCanvas graph={g} then={at ? then.data : null} selected={selected} focus={focus} onSelect={(id) => go({ entity: id, day: "" })} />
              )
            }
          </ReadView>
        </div>
        <TimeScrubber onDay={(d) => go({ day: d, entity: "" })} />
      </div>
      <aside className="nt-graph__side" aria-label="Details">
        {day ? <DayPanel date={day} onClose={() => go({ day: "" })} /> : selected ? <EntityPanel id={selected} at={at} onFocus={() => go({ focus: selected, entity: selected })} /> : <p className="nt-muted nt-pad">Pick a person, project or idea to see how it evolved, then choose a date to see what you knew then.</p>}
      </aside>
    </div>
  );
}

function FocusBar({ focus, depth, onFocus, onDepth, asList, onList }: { focus: string[]; depth: number; onFocus: (ids: string[]) => void; onDepth: (d: number) => void; asList: boolean; onList: (list: boolean) => void }) {
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
      <div className="nt-segmented nt-focusbar__end" role="radiogroup" aria-label="Show as">
        <button type="button" role="radio" aria-checked={!asList} className="nt-segmented__opt" onClick={() => onList(false)}>
          Picture
        </button>
        <button type="button" role="radio" aria-checked={asList} className="nt-segmented__opt" onClick={() => onList(true)}>
          List
        </button>
      </div>
    </div>
  );
}

/** The same connections as a readable table (every graph has its list; keyboard and screen readers). */
function RelationList({ graph, then, onSelect }: { graph: Graph; then: Graph | null; onSelect: (id: string) => void }) {
  const names = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n.name])), [graph]);
  const thenNodes = useMemo(() => (then ? new Set(then.nodes.map((n) => n.id)) : null), [then]);
  const thenEdges = useMemo(() => (then ? new Map(then.edges.map((e) => [`${e.source}|${e.target}|${e.predicate}`, e])) : null), [then]);
  const linked = new Set(graph.edges.flatMap((e) => [e.source, e.target]));
  const alone = graph.nodes.filter((n) => !linked.has(n.id));
  const rows = [...graph.edges].sort((a, b) => (names.get(a.source) || "").localeCompare(names.get(b.source) || "") || predicateWords(a.predicate).localeCompare(predicateWords(b.predicate)));
  const entity = (id: string) => (
    <button type="button" className="nt-link" onClick={() => onSelect(id)}>
      <span className="nt-link__title">{names.get(id) || "Someone"}</span>
    </button>
  );
  return (
    <div className="nt-rels__wrap">
      <table className="nt-rels">
        <caption>{`${graph.edges.length} ${graph.edges.length === 1 ? "link" : "links"} between ${graph.nodes.length} people, projects and ideas`}</caption>
        <thead>
          <tr>
            <th scope="col">From</th>
            <th scope="col">Link</th>
            <th scope="col">To</th>
            <th scope="col">Since</th>
            <th scope="col">State</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => {
            const te = thenEdges ? thenEdges.get(`${e.source}|${e.target}|${e.predicate}`) : e;
            const state = !te ? "not known yet" : te.active ? "believed" : "no longer believed";
            return (
              <tr key={`${e.source}|${e.target}|${e.predicate}`} className={!te ? "nt-rels__future" : !te.active ? "nt-rels__old" : undefined}>
                <td>{entity(e.source)}</td>
                <td>
                  <button type="button" className="nt-link" onClick={() => openRelation(e.source, e.target)} title="How this link changed">
                    <span className="nt-link__title">{`${predicateWords(e.predicate)}${(te?.count ?? e.count) > 1 ? ` ×${te?.count ?? e.count}` : ""}`}</span>
                  </button>
                </td>
                <td>{entity(e.target)}</td>
                <td className="nt-link__date">{shortDate(e.first_seen)}</td>
                <td>{state}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {alone.length ? (
        <p className="nt-chips">
          <span className="nt-muted">Not linked yet:</span>
          {alone.map((n) => (
            <AfChipButton key={n.id} size="sm" tone="muted" onClick={() => onSelect(n.id)} ariaLabel={`${n.name}${thenNodes && !thenNodes.has(n.id) ? ", not known yet at that time" : ""}`}>
              {n.name}
            </AfChipButton>
          ))}
        </p>
      ) : null}
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

type PanelTab = "evolved" | "changes" | "actions" | "sources" | "links";

/** The topic portrait: how it evolved (first), changes, open actions, sources and links. */
export function EntityPanel({ id, at, onFocus }: { id: string; at: string | null; onFocus?: () => void }) {
  const { api } = useApp();
  const [, setAt] = useAt();
  const [tab, setTab] = useState<PanelTab>("evolved");
  const card = useRead<EntityCard>(() => api.get<EntityCard>(`note/entities/${encodeURIComponent(id)}`, { at }), [api, id, at]);
  const timeline = useRead<{ items: TimelineItem[] }>(() => api.get<{ items: TimelineItem[] }>(`note/entities/${encodeURIComponent(id)}/timeline`), [api, id]);
  const thenNow = useRead<ThenNowRaw>(() => api.get<ThenNowRaw>(`note/entities/${encodeURIComponent(id)}/then-now`), [api, id]);
  // Milestones come from the card as it is now, so the list keeps its shape when the date changes.
  const current = useRead<EntityCard | null>(() => (at ? api.get<EntityCard>(`note/entities/${encodeURIComponent(id)}`) : Promise.resolve(null)), [api, id, Boolean(at)]);
  const tn = useMemo(() => thenNowView(thenNow.data), [thenNow.data]);
  const noteDates = useMemo(() => (timeline.data?.items || []).filter((i) => i.type === "note").map((i) => i.at), [timeline.data]);
  const range = useMemo(() => {
    const ts = noteDates.map((d) => Date.parse(d)).filter(Number.isFinite);
    const to = Date.now();
    const from = Math.min(to - 90 * 86400_000, ...ts);
    return { from, to };
  }, [noteDates]);
  const steps = useMemo(() => milestones(at ? current.data : card.data, tn, timeline.data?.items), [at, current.data, card.data, tn, timeline.data]);

  // Asked for a moment before this entry existed: say so, plainly (the server refuses that read).
  if (at && card.data === null && !card.loading && card.error instanceof ApiError && (card.error.status === 400 || card.error.status === 404)) {
    return (
      <div className="nt-panel">
        <p className="nt-notyet">{`This was not in your notebook yet on ${longDate(at)}.`}</p>
        <div className="nt-actions">
          <button type="button" className="nt-btn" onClick={() => setAt(null)}>
            Back to now
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="nt-panel">
      <ReadView read={card} what="this">
        {(e) => {
          const tabs = [
            { id: "evolved", label: "How it evolved" },
            { id: "changes", label: "Changes" },
            { id: "actions", label: "Open actions" },
            { id: "sources", label: "Sources" },
            { id: "links", label: "Links" },
          ];
          return (
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
              {/* With a summary history, the latest view closes "How it evolved"; without one the summary shows here. */}
              {e.summary && !(e.summary_history || []).length ? <p className="nt-summary">{e.summary}</p> : null}
              <p className="nt-muted">
                {e.note_count ?? 0} {e.note_count === 1 ? "note" : "notes"}
                {e.first_seen ? ` · first ${shortDate(e.first_seen)}` : ""}
                {e.last_seen ? ` · latest ${shortDate(e.last_seen)}` : ""}
              </p>
              <AfTabs tabs={tabs} value={tab} onChange={(v) => setTab(v as PanelTab)} ariaLabel={`About ${e.name}`} idBase={`nt-entity-${e.entity_id.replace(/[^A-Za-z0-9_-]/g, "-")}`}>
                {tab === "evolved" ? <Evolved steps={steps} at={at} loading={timeline.loading || thenNow.loading} /> : null}
                {tab === "changes" ? (
                  <>
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
                    ) : (
                      <p className="nt-muted">Nothing stated about it yet.</p>
                    )}
                    {tn && (tn.earliest || tn.latest) ? (
                      <Section title="Then and now">
                        <ThenNowCard data={{ ...tn, turning: [] }} />
                      </Section>
                    ) : null}
                  </>
                ) : null}
                {tab === "actions" ? (
                  (e.open_tasks || []).length ? (
                    <ul className="nt-list">
                      {(e.open_tasks || []).map((t) => (
                        <li key={t.task_id} className="nt-item">
                          <p className="nt-item__title">{t.text}</p>
                          {t.due ? <p className="nt-muted">Due {longDate(t.due)}</p> : null}
                          <div className="nt-actions">
                            <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => openNote(t.note_id)}>
                              Open note
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="nt-muted">No open actions.</p>
                  )
                ) : null}
                {tab === "sources" ? (
                  (e.notes || []).length ? (
                    <ul className="nt-list">
                      {(e.notes || []).map((n) => (
                        <li key={n.note_id} className="nt-list__row">
                          <NoteLink note={n} />
                          <KindBadge kind={n.kind} />
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="nt-muted">No notes yet.</p>
                  )
                ) : null}
                {tab === "links" ? (
                  (e.relations || []).length ? (
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
                  ) : (
                    <p className="nt-muted">Not linked to anything yet.</p>
                  )
                ) : null}
              </AfTabs>
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
          );
        }}
      </ReadView>
    </div>
  );
}

/** Milestones, oldest first; those after the chosen date stay in place, faded, so nothing jumps. */
function Evolved({ steps, at, loading }: { steps: ReturnType<typeof milestones>; at: string | null; loading: boolean }) {
  if (!steps.length) return <p className="nt-muted">{loading ? "Loading…" : "One note so far; its history starts here."}</p>;
  const cut = at ? Date.parse(at) : Infinity;
  return (
    <ol className="nt-milestones" aria-label="How it evolved, oldest first">
      {steps.map((m, i) => {
        const later = Date.parse(m.at) > cut;
        return (
          <li key={`${m.kind}:${m.at}:${i}`} className={`nt-milestone nt-milestone--${m.kind}${later ? " nt-milestone--later" : ""}`}>
            <div className="nt-milestone__head">
              <span className="nt-milestone__label">{later ? `${m.label} · later` : m.label}</span>
              <time className="nt-milestone__date" dateTime={m.at}>
                {longDate(m.at)}
              </time>
            </div>
            <p className="nt-milestone__text">{capitalize(m.text)}</p>
            {m.note_id || m.claim_id ? (
              <div className="nt-actions">
                {m.note_id ? (
                  <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => openNote(m.note_id!)}>
                    Open note
                  </button>
                ) : null}
                {m.claim_id ? (
                  <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => openClaim(m.claim_id!)}>
                    Why
                  </button>
                ) : null}
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
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
