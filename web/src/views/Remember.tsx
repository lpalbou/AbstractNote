import React, { useEffect, useMemo, useRef, useState } from "react";
import { AfChip, AfChipButton, Icon } from "@abstractframework/ui-kit";
import { ApiError, errorSentence } from "../lib/api";
import { splitCitations } from "../lib/citations";
import { useDebounced, useRead } from "../lib/hooks";
import { CURSOR_KEY, readLocal, writeLocal } from "../lib/local";
import { noteAbout, useAt, type Route } from "../lib/nav";
import { followRun, runErrorText, startFlow } from "../lib/runs";
import type { Changes, Cue, EntityCard, EntityListItem, Insight, ListOf, NoteRef, Overview, RecallOutput, RelationTimeline, SearchResult, Task, ThenNowRaw, Topic } from "../lib/types";
import { claimsById, thenNowView } from "../lib/shapes";
import { NOTE_KINDS, claimSentence, longDate, relative, shortDate, topicLabel } from "../lib/words";
import { EntityChip, NoteLink, ReadView, Section, Sparkline, openClaim, openEntity, openNote, useApp } from "../components/common";
import { ThenNowCard } from "../components/ThenNow";
import { TimeScrubber } from "../components/TimeScrubber";

type Effort = "quick" | "standard" | "deep";
const EFFORTS: Array<[Effort, string, string]> = [
  ["quick", "Quick", "Matching notes, instantly"],
  ["standard", "Standard", "An answer with sources, in seconds"],
  ["deep", "Deep", "A full dossier, in a few minutes"],
];

export function RememberView({ route }: { route: Route }) {
  return (
    <div className="nt-remember">
      <SinceLastLooked />
      <CueCards />
      <AskPanel initialTopic={route.params.get("topic") || ""} />
      <ThenVersusNow />
      <Revisit />
    </div>
  );
}

// ---------------------------------------------------------------- since you last looked

function SinceLastLooked() {
  const { api } = useApp();
  // The cursor is opaque (the server's); the moment it was taken is kept beside it for the "Since …" line.
  const [mark, setMark] = useState(() => readMark());
  const since = mark.cursor;
  const changes = useRead<Changes>(() => api.get<Changes>("note/changes", { since, limit: 50 }), [api, since]);
  const count = (c: Changes) => (c.notes?.length || 0) + (c.entities?.length || 0) + (c.superseded_claims?.length || 0) + (c.closed_loops?.length || 0) + (c.failed_notes?.length || 0) + (c.insights?.length || 0);
  return (
    <Section
      id="since"
      title="Since you last looked"
      actions={
        changes.data && count(changes.data) ? (
          <button
            type="button"
            className="nt-btn nt-btn--quiet"
            onClick={() => {
              const next = { cursor: changes.data!.cursor, at: new Date().toISOString() };
              writeLocal(CURSOR_KEY, JSON.stringify(next));
              setMark(next);
            }}
          >
            Mark as seen
          </button>
        ) : null
      }
    >
      <p className="nt-muted">Since {longDate(mark.at)}.</p>
      <ReadView read={changes} what="what changed" empty={(c) => (count(c) ? false : "Nothing new since then.")}>
        {(c) => (
          <div className="nt-changes">
            {c.insights?.length ? (
              <ChangeGroup title="Insights">
                {c.insights.map((i) => (
                  <NoteLink key={i.note_id} note={i} />
                ))}
              </ChangeGroup>
            ) : null}
            {c.superseded_claims?.length ? (
              <ChangeGroup title="Changed beliefs">
                {c.superseded_claims.map((cl) => (
                  <button key={cl.claim_id} type="button" className="nt-link" onClick={() => openClaim(cl.claim_id)}>
                    <span className="nt-link__title nt-struck">{cl.text}</span>
                    <span className="nt-link__date">{cl.kind === "retracted" ? "Withdrawn" : "Replaced"}</span>
                  </button>
                ))}
              </ChangeGroup>
            ) : null}
            {c.entities?.length ? (
              <ChangeGroup title="New in your notebook">
                <div className="nt-chips">
                  {c.entities.map((e) => (
                    <EntityChip key={e.entity_id} entity={e} />
                  ))}
                </div>
              </ChangeGroup>
            ) : null}
            {c.closed_loops?.length ? (
              <ChangeGroup title="Closed loops">
                {c.closed_loops.map((t) => (
                  <p key={t.task_id} className="nt-done">
                    <Icon name="check" size={14} /> {t.text}
                  </p>
                ))}
              </ChangeGroup>
            ) : null}
            {c.failed_notes?.length ? (
              <ChangeGroup title="Could not be processed">
                {c.failed_notes.map((n) => (
                  <div key={n.note_id}>
                    <NoteLink note={n} showDate={false} />
                    {n.error ? <p className="nt-filing__error">{n.error}</p> : null}
                  </div>
                ))}
                <p className="nt-muted">Retry them from Settings.</p>
              </ChangeGroup>
            ) : null}
            {c.notes?.length ? (
              <ChangeGroup title={`New notes (${c.notes.length})`}>
                {c.notes.slice(0, 6).map((n) => (
                  <NoteLink key={n.note_id} note={n} />
                ))}
              </ChangeGroup>
            ) : null}
          </div>
        )}
      </ReadView>
    </Section>
  );
}

/** The stored "last looked" mark; first visit = one week back (an ISO moment is a valid `since`). */
export function readMark(): { cursor: string; at: string } {
  try {
    const m = JSON.parse(readLocal(CURSOR_KEY) || "null");
    if (m && typeof m.cursor === "string" && typeof m.at === "string") return m;
  } catch {
    // an older or broken value: start one week back
  }
  const at = new Date(Date.now() - 7 * 86400_000).toISOString().replace(/\.\d{3}Z$/, "Z");
  return { cursor: at, at };
}

function ChangeGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="nt-change">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------- cue cards

function CueCards() {
  const { api } = useApp();
  const cues = useRead<ListOf<Cue>>(() => api.get<ListOf<Cue>>("note/cues", { limit: 3 }), [api]);
  return (
    <Section id="cues" title="Worth a thought">
      <ReadView read={cues} what="your prompts" empty={(c) => (c.items.length ? false : "No prompts right now.")}>
        {(c) => (
          <div className="nt-cards">
            {c.items.map((cue) => (
              <article key={cue.cue_id} className="nt-card">
                <p>{cue.text}</p>
                <div className="nt-actions">
                  {cue.subject?.kind === "entity" ? (
                    <button type="button" className="nt-btn nt-btn--quiet" onClick={() => openEntity(cue.subject.id)}>
                      {`Open ${cue.subject.title}`}
                    </button>
                  ) : null}
                  <button type="button" className="nt-btn nt-btn--primary" onClick={() => noteAbout("cue", cue.cue_id, cue.text)}>
                    Answer
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </ReadView>
    </Section>
  );
}

// ---------------------------------------------------------------- ask

type Asked = { question: string; effort: Effort; state: "asking" | "answered" | "failed"; output?: RecallOutput; error?: string };

function AskPanel({ initialTopic }: { initialTopic: string }) {
  const { api, sessionId } = useApp();
  const [at, setAt] = useAt();
  const [q, setQ] = useState("");
  const [effort, setEffort] = useState<Effort>("standard");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [kinds, setKinds] = useState<string[]>([]);
  const [topics, setTopics] = useState<string[]>(initialTopic ? [initialTopic] : []);
  const [entities, setEntities] = useState<EntityListItem[]>([]);
  const [open, setOpen] = useState<"" | "when" | "who" | "kind" | "topic">("");
  const [asked, setAsked] = useState<Asked | null>(null);
  const [saved, setSaved] = useState("");
  const dq = useDebounced(q.trim(), 300);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (initialTopic) setTopics([initialTopic]);
  }, [initialTopic]);

  const filters = { since: since ? `${since}T00:00:00Z` : undefined, until: until ? `${until}T23:59:59Z` : at || undefined, kinds, topics, entities: entities.map((e) => e.entity_id) };
  const hasFilter = Boolean(since || until || kinds.length || topics.length || entities.length);
  // Quick recall needs words (GET /note/search requires q); cue chips alone list the matching notes.
  const quick = useRead<SearchResult | null>(() => (dq ? api.get<SearchResult>("note/search", { q: dq, ...filters, limit: 12 }) : hasFilter ? listNotes() : Promise.resolve(null)), [api, dq, since, until, at, kinds.join(), topics.join(), entities.map((e) => e.entity_id).join()]);

  async function listNotes(): Promise<SearchResult> {
    const page = await api.get<ListOf<NoteRef>>("note/notes", { since: filters.since, until: filters.until, kind: kinds[0], topic: topics[0], entity: filters.entities[0], limit: 12 });
    return { notes: page.items, entities: [], claims: [] };
  }

  const ask = async () => {
    const question = q.trim();
    if (!question || effort === "quick") return;
    setSaved("");
    setAsked({ question, effort, state: "asking" });
    try {
      const runId = await startFlow(api, "note-recall", sessionId, { question, effort, as_of: at, since: filters.since ?? null, until: filters.until ?? null, entities: filters.entities, topics });
      const run = await followRun(api, runId);
      if (!alive.current) return;
      const refused = runErrorText(run);
      if (refused !== null) throw new Error(refused || "not completed");
      setAsked({ question, effort, state: "answered", output: (run.output || {}) as RecallOutput });
    } catch (e) {
      if (alive.current) setAsked({ question, effort, state: "failed", error: e instanceof ApiError ? errorSentence(e, "the answer") : e instanceof Error && e.message !== "not completed" ? e.message : "This question could not be answered." });
    }
  };

  const saveAnswer = async (a: Asked) => {
    const answer = a.output?.answer || a.output?.response || "";
    setSaved("Saving…");
    try {
      const runId = await startFlow(api, "note-capture", sessionId, {
        text: `Q: ${a.question}\n\n${answer}`,
        occurred_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
        source: { client: "web" },
        idempotency_key: crypto.randomUUID(),
        process_inline: false,
        context: { attachments: [], answer: { question: a.question, evidence_note_ids: (a.output?.evidence || []).map((e) => e.note_id) } },
      });
      const run = await followRun(api, runId);
      setSaved(runErrorText(run) === null ? "Saved as a note." : "The answer could not be saved.");
    } catch {
      setSaved("The answer could not be saved.");
    }
  };

  return (
    <Section id="ask" title="Ask your notes">
      <form
        className="nt-ask"
        onSubmit={(e) => {
          e.preventDefault();
          void ask();
        }}
      >
        <div className="nt-ask__bar">
          <Icon name="history" size={18} />
          <input className="nt-input nt-input--bare" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="What did I decide about…" aria-label="Question" />
          <button type="submit" className="nt-btn nt-btn--primary" disabled={!q.trim() || effort === "quick" || asked?.state === "asking"}>
            {asked?.state === "asking" ? "Asking…" : "Ask"}
          </button>
        </div>
        <div className="nt-effort" role="radiogroup" aria-label="Effort">
          {EFFORTS.map(([v, label, hint]) => (
            <button key={v} type="button" role="radio" aria-checked={effort === v} className="nt-effort__opt" title={hint} onClick={() => setEffort(v)}>
              {label}
            </button>
          ))}
          <span className="nt-muted nt-effort__hint">{EFFORTS.find(([v]) => v === effort)![2]}</span>
        </div>
        <div className="nt-chips" aria-label="Cues">
          <AfChipButton size="sm" pressed={Boolean(since || until)} expanded={open === "when"} onClick={() => setOpen(open === "when" ? "" : "when")}>
            {since || until ? `When: ${since ? shortDate(since) : "…"} – ${until ? shortDate(until) : "now"}` : "When"}
          </AfChipButton>
          <AfChipButton size="sm" pressed={entities.length > 0} expanded={open === "who"} onClick={() => setOpen(open === "who" ? "" : "who")}>
            {entities.length ? `Who or what: ${entities.map((e) => e.name).join(", ")}` : "Who or what"}
          </AfChipButton>
          <AfChipButton size="sm" pressed={kinds.length > 0} expanded={open === "kind"} onClick={() => setOpen(open === "kind" ? "" : "kind")}>
            {kinds.length ? `Kind: ${kinds.join(", ")}` : "Kind"}
          </AfChipButton>
          <AfChipButton size="sm" pressed={topics.length > 0} expanded={open === "topic"} onClick={() => setOpen(open === "topic" ? "" : "topic")}>
            {topics.length ? `Topic: ${topics.map(topicLabel).join(", ")}` : "Topic"}
          </AfChipButton>
          {at ? (
            <AfChip size="sm" tone="info" onRemove={() => setAt(null)} removeLabel="Back to now">
              {`As of ${shortDate(at)}`}
            </AfChip>
          ) : null}
        </div>
        {open === "when" ? (
          <div className="nt-cue-panel">
            <label className="nt-field nt-field--inline">
              <span className="nt-field__label">From</span>
              <input className="nt-input" type="date" value={since} onChange={(e) => setSince(e.target.value)} />
            </label>
            <label className="nt-field nt-field--inline">
              <span className="nt-field__label">To</span>
              <input className="nt-input" type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
            </label>
            <TimeScrubber />
          </div>
        ) : null}
        {open === "who" ? <EntityPicker chosen={entities} onChange={setEntities} /> : null}
        {open === "kind" ? (
          <div className="nt-cue-panel nt-chips">
            {NOTE_KINDS.map((k) => (
              <AfChipButton key={k} size="sm" pressed={kinds.includes(k)} onClick={() => setKinds(kinds.includes(k) ? kinds.filter((x) => x !== k) : [...kinds, k])}>
                {k}
              </AfChipButton>
            ))}
          </div>
        ) : null}
        {open === "topic" ? <TopicPicker chosen={topics} onChange={setTopics} /> : null}
      </form>
      {asked ? <AnswerCard asked={asked} onSave={() => void saveAnswer(asked)} saved={saved} onFollowUp={(f) => setQ(f)} /> : null}
      {quick.data || quick.loading ? (
        <div className="nt-quick" aria-live="polite">
          {quick.loading && !quick.data ? null : (
            <ReadView read={quick} what="matching notes">
              {(r) => (r ? <QuickResults result={r} /> : null)}
            </ReadView>
          )}
        </div>
      ) : null}
    </Section>
  );
}

function EntityPicker({ chosen, onChange }: { chosen: EntityListItem[]; onChange: (next: EntityListItem[]) => void }) {
  const { api } = useApp();
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 200);
  const list = useRead<ListOf<EntityListItem>>(() => api.get<ListOf<EntityListItem>>("note/entities", { q: dq, limit: 12, sort: dq ? "frequent" : "recent" }), [api, dq]);
  return (
    <div className="nt-cue-panel">
      <input className="nt-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a person, project, idea…" aria-label="Find who or what" />
      <ReadView read={list} what="the list">
        {(l) => (
          <div className="nt-chips">
            {l.items.map((e) => {
              const on = chosen.some((c) => c.entity_id === e.entity_id);
              return (
                <AfChipButton key={e.entity_id} size="sm" pressed={on} onClick={() => onChange(on ? chosen.filter((c) => c.entity_id !== e.entity_id) : [...chosen, e])}>
                  {e.name}
                </AfChipButton>
              );
            })}
          </div>
        )}
      </ReadView>
    </div>
  );
}

function TopicPicker({ chosen, onChange }: { chosen: string[]; onChange: (next: string[]) => void }) {
  const { api } = useApp();
  const list = useRead<ListOf<Topic>>(() => api.get<ListOf<Topic>>("note/topics"), [api]);
  return (
    <div className="nt-cue-panel">
      <ReadView read={list} what="your topics" empty={(l) => (l.items.length ? false : "No topics yet.")}>
        {(l) => (
          <div className="nt-chips">
            {l.items.map((t) => (
              <AfChipButton key={t.topic} size="sm" pressed={chosen.includes(t.topic)} onClick={() => onChange(chosen.includes(t.topic) ? chosen.filter((x) => x !== t.topic) : [...chosen, t.topic])}>
                {`#${topicLabel(t.topic)} · ${t.count}`}
              </AfChipButton>
            ))}
          </div>
        )}
      </ReadView>
    </div>
  );
}

function QuickResults({ result }: { result: SearchResult }) {
  if (!result.notes.length && !result.entities.length) return <p className="nt-muted">No notes match.</p>;
  return (
    <div className="nt-quick__grid">
      {result.entities.length ? (
        <div className="nt-chips">
          {result.entities.map((e) => (
            <EntityChip key={e.entity_id} entity={e} />
          ))}
        </div>
      ) : null}
      <ul className="nt-hits">
        {result.notes.map((n) => (
          <li key={n.note_id}>
            <button type="button" className="nt-hit" onClick={() => openNote(n.note_id)}>
              <span className="nt-hit__head">
                <span className={`nt-kind nt-kind--${n.kind}`}>{n.kind}</span>
                <span className="nt-hit__title">{n.title}</span>
                <span className="nt-link__date">{shortDate(n.occurred_at)}</span>
              </span>
              {n.excerpt ? <span className="nt-hit__excerpt">{n.excerpt}</span> : null}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AnswerCard({ asked, onSave, saved, onFollowUp }: { asked: Asked; onSave: () => void; saved: string; onFollowUp: (q: string) => void }) {
  if (asked.state === "asking") {
    return (
      <div className="nt-answer-card" role="status">
        <p className="nt-loading">
          <Icon name="loader" size={16} className="nt-spin" />
          <span>{asked.effort === "deep" ? "Digging through your notes. A dossier takes a few minutes; it also appears under Revisit when it is ready." : "Thinking it through…"}</span>
        </p>
      </div>
    );
  }
  if (asked.state === "failed") {
    return (
      <div className="nt-answer-card" role="alert">
        <p className="nt-filing__error">{asked.error}</p>
      </div>
    );
  }
  const out = asked.output || {};
  const evolution = thenNowView(out.evolution);
  const text = out.answer || out.response || "";
  const { segments, cited } = splitCitations(text);
  const evidence = out.evidence || [];
  const titleOf = (id: string) => evidence.find((e) => e.note_id === id)?.title || "a note";
  return (
    <article className="nt-answer-card">
      <p className="nt-answer-text">
        {segments.map((s, i) =>
          "cite" in s ? (
            <button key={i} type="button" className="nt-cite" title={`Open “${titleOf(s.noteId)}”`} aria-label={`Source ${s.cite}: ${titleOf(s.noteId)}`} onClick={() => openNote(s.noteId)}>
              {s.cite}
            </button>
          ) : (
            <span key={i}>{s.text}</span>
          ),
        )}
      </p>
      {cited.length || evidence.length ? (
        <div className="nt-block">
          <h3>Sources</h3>
          <ol className="nt-sources">
            {(cited.length ? cited : evidence.map((e) => e.note_id)).map((id) => {
              const ev = evidence.find((e) => e.note_id === id);
              return (
                <li key={id}>
                  <NoteLink note={{ note_id: id, title: ev?.title || "A note", occurred_at: ev?.occurred_at }} />
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
      {evolution ? <ThenNowCard data={evolution} title="How it changed" /> : null}
      {(out.open_loops || []).length ? (
        <div className="nt-block">
          <h3>Still open</h3>
          <ul className="nt-list">
            {(out.open_loops || []).map((t) => (
              <li key={t.task_id}>{t.text}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {(out.follow_ups || []).length ? (
        <div className="nt-chips">
          {(out.follow_ups || []).map((f) => (
            <AfChipButton key={f} size="sm" onClick={() => onFollowUp(f)}>
              {f}
            </AfChipButton>
          ))}
        </div>
      ) : null}
      <div className="nt-actions">
        {out.dossier_note_id ? (
          <button type="button" className="nt-btn" onClick={() => openNote(out.dossier_note_id!)}>
            Open the dossier
          </button>
        ) : null}
        <button type="button" className="nt-btn" onClick={onSave} disabled={saved === "Saving…"}>
          Save this answer
        </button>
        {saved ? (
          <span className="nt-muted" role="status">
            {saved}
          </span>
        ) : null}
      </div>
    </article>
  );
}

// ---------------------------------------------------------------- then versus now

function ThenVersusNow() {
  const { api } = useApp();
  const overview = useRead<Overview>(() => api.get<Overview>("note/overview", { days: 90 }), [api]);
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const candidates = overview.data?.entities || [];
  const first = a || candidates[0]?.entity_id || "";
  return (
    <Section id="thennow" title="Then and now">
      <ReadView read={overview} what="your entities" empty={(o) => ((o.entities || []).length ? false : "Then and now appears once your notes mention someone or something twice.")}>
        {(o) => (
          <>
            <div className="nt-pickers">
              <label className="nt-field nt-field--inline">
                <span className="nt-field__label">About</span>
                <select className="nt-input" value={first} onChange={(e) => setA(e.target.value)}>
                  {(o.entities || []).map((e) => (
                    <option key={e.entity_id} value={e.entity_id}>
                      {e.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="nt-field nt-field--inline">
                <span className="nt-field__label">and its link with</span>
                <select className="nt-input" value={b} onChange={(e) => setB(e.target.value)}>
                  <option value="">nobody in particular</option>
                  {(o.entities || [])
                    .filter((e) => e.entity_id !== first)
                    .map((e) => (
                      <option key={e.entity_id} value={e.entity_id}>
                        {e.name}
                      </option>
                    ))}
                </select>
              </label>
            </div>
            {first && !b ? <EntityThenNow id={first} /> : null}
            {first && b ? <RelationThenNow a={first} b={b} /> : null}
          </>
        )}
      </ReadView>
    </Section>
  );
}

function EntityThenNow({ id }: { id: string }) {
  const { api } = useApp();
  const tn = useRead<ThenNowRaw>(() => api.get<ThenNowRaw>(`note/entities/${encodeURIComponent(id)}/then-now`), [api, id]);
  const card = useRead<EntityCard>(() => api.get<EntityCard>(`note/entities/${encodeURIComponent(id)}`), [api, id]);
  const dates = useMemo(() => (card.data?.notes || []).map((n) => n.occurred_at), [card.data]);
  const to = Date.now();
  const from = Math.min(to - 90 * 86400_000, ...dates.map((d) => Date.parse(d)).filter(Number.isFinite));
  return (
    <div className="nt-thennow-wrap">
      {card.data ? (
        <div className="nt-entity-line">
          <button type="button" className="nt-link" onClick={() => openEntity(id)}>
            <span className="nt-link__title">{card.data.name}</span>
            <span className="nt-link__date">
              {card.data.note_count === 1 ? "1 note" : `${card.data.note_count ?? 0} notes`} · latest {relative(card.data.last_seen)}
            </span>
          </button>
          <Sparkline dates={dates} from={from} to={to} label={`${card.data.name} activity`} />
        </div>
      ) : null}
      <ReadView read={tn} what="then and now" empty={(d) => (thenNowView(d) ? false : "Nothing has changed yet.")}>
        {(d) => <ThenNowCard data={thenNowView(d)!} />}
      </ReadView>
    </div>
  );
}

function RelationThenNow({ a, b }: { a: string; b: string }) {
  const { api } = useApp();
  const rel = useRead<RelationTimeline>(() => api.get<RelationTimeline>(`note/relations/${encodeURIComponent(a)}/${encodeURIComponent(b)}`), [api, a, b]);
  return (
    <ReadView read={rel} what="this link" empty={(r) => (r.claims.length ? false : "Your notes do not link these two yet.")}>
      {(r) => (
        <div className="nt-thennow-wrap">
          {(() => {
            const v = thenNowView(r.then_now, claimsById(r.claims));
            return v ? <ThenNowCard data={v} /> : null;
          })()}
          <ol className="nt-timeline">
            {r.claims.map((c) => (
              <li key={c.claim_id} className={c.status !== "active" ? "nt-timeline__item nt-timeline__item--old" : "nt-timeline__item"}>
                <span className="nt-timeline__date">{shortDate(c.observed_at)}</span>
                <button type="button" className="nt-link" onClick={() => openClaim(c.claim_id)}>
                  <span className="nt-link__title">{claimSentence(c)}</span>
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}
    </ReadView>
  );
}

// ---------------------------------------------------------------- revisit

function Revisit() {
  const { api } = useApp();
  const insights = useRead<ListOf<Insight>>(() => api.get<ListOf<Insight>>("note/insights", { acked: false, limit: 10 }), [api]);
  const tasks = useRead<ListOf<Task>>(() => api.get<ListOf<Task>>("note/tasks", { status: "open", limit: 20 }), [api]);
  const overview = useRead<Overview>(() => api.get<Overview>("note/overview", { days: 30 }), [api]);
  const [busy, setBusy] = useState("");
  const [line, setLine] = useState("");
  const act = async (key: string, path: string, done: string, after: () => void, body?: unknown) => {
    setBusy(key);
    setLine("");
    try {
      await api.post(path, body);
      setLine(done);
      after();
    } catch (e) {
      setLine(errorSentence(e, "that").replace("could not be loaded", "did not work"));
    } finally {
      setBusy("");
    }
  };
  return (
    <Section id="revisit" title="Revisit">
      {line ? (
        <p className="nt-muted" role="status">
          {line}
        </p>
      ) : null}
      <div className="nt-revisit">
        <div className="nt-change">
          <h3>Insights</h3>
          <ReadView read={insights} what="your insights" empty={(l) => (l.items.length ? false : "Nothing new to read.")}>
            {(l) => (
              <ul className="nt-list">
                {l.items.map((i) => (
                  <li key={i.note_id} className="nt-item">
                    <p className="nt-item__title">{i.title}</p>
                    {i.body ? <p className="nt-muted">{i.body}</p> : null}
                    <div className="nt-actions">
                      <button type="button" className="nt-btn nt-btn--quiet" onClick={() => openNote(i.note_id)}>
                        Open
                      </button>
                      <button type="button" className="nt-btn nt-btn--quiet" onClick={() => noteAbout("insight", i.note_id, i.title)}>
                        Add a note
                      </button>
                      <button type="button" className="nt-btn" disabled={busy === i.note_id} onClick={() => void act(i.note_id, `note/insights/${encodeURIComponent(i.note_id)}/ack`, "Marked as read.", insights.reload)}>
                        {busy === i.note_id ? "Saving…" : "Got it"}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </ReadView>
        </div>
        <div className="nt-change">
          <h3>Open loops</h3>
          <ReadView read={tasks} what="your open loops" empty={(l) => (l.items.length ? false : "No open loops.")}>
            {(l) => (
              <ul className="nt-list">
                {l.items.map((t) => (
                  <li key={t.task_id} className="nt-item">
                    <p className="nt-item__title">{t.text}</p>
                    {t.due ? <p className="nt-muted">Due {longDate(t.due)}</p> : null}
                    <div className="nt-actions">
                      <button type="button" className="nt-btn nt-btn--quiet" onClick={() => openNote(t.note_id)}>
                        Open
                      </button>
                      <button type="button" className="nt-btn nt-btn--quiet" onClick={() => noteAbout("task", t.task_id, t.text)}>
                        Add a note
                      </button>
                      <button type="button" className="nt-btn" disabled={busy === t.task_id} onClick={() => void act(t.task_id, `note/tasks/${encodeURIComponent(t.task_id)}/close`, `Closed: ${t.text}.`, tasks.reload, {})}>
                        {busy === t.task_id ? "Closing…" : "Close"}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </ReadView>
        </div>
        <div className="nt-change">
          <h3>Worth another look</h3>
          <ReadView read={overview} what="this" empty={(o) => ((o.resurfacing || []).length ? false : "Nothing to resurface today.")}>
            {(o) => (
              <ul className="nt-list">
                {(o.resurfacing || []).map((n) => (
                  <li key={n.note_id} className="nt-item">
                    <NoteLink note={n} />
                    {n.excerpt ? <p className="nt-muted">{n.excerpt}</p> : null}
                    <div className="nt-actions">
                      <button type="button" className="nt-btn" onClick={() => noteAbout("note", n.note_id, n.title)}>
                        Add a note
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </ReadView>
        </div>
      </div>
    </Section>
  );
}

