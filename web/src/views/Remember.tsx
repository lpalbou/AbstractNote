import React, { useEffect, useMemo, useRef, useState } from "react";
import { AfChip, AfChipButton, Icon } from "@abstractframework/ui-kit";
import { ApiError, errorSentence } from "../lib/api";
import { splitCitations } from "../lib/citations";
import { useDebounced, useRead, type ReadState } from "../lib/hooks";
import { CURSOR_KEY, readLocal, writeLocal } from "../lib/local";
import { noteAbout, useAt, type Route } from "../lib/nav";
import { fetchReview, reviewAction, runReviewAction } from "../lib/review";
import { followRun, runErrorText, startFlow, stepWords } from "../lib/runs";
import type { Changes, Cue, EntityListItem, EntityCard, EntityRecord, Insight, ListOf, Note, NoteRef, NoteStatus, Overview, RecallOutput, RelationTimeline, ReviewAction, ReviewItem, ReviewSide, SearchResult, Task, ThenNowRaw, Topic } from "../lib/types";
import { claimsById, thenNowView, uniqueEntities } from "../lib/shapes";
import { NOTE_KINDS, claimSentence, longDate, relative, shortDate, topicLabel } from "../lib/words";
import { basisLine, reviewWhy, whyLine } from "../lib/why";
import { Disclose, EmptyNotebook, EntityChip, KindBadge, NoteLink, ReadView, Section, Sparkline, openClaim, openEntity, openNote, useApp } from "../components/common";
import { ThenNowCard } from "../components/ThenNow";
import { TimeScrubber } from "../components/TimeScrubber";

type Effort = "quick" | "standard" | "deep";
const EFFORTS: Array<[Effort, string, string]> = [
  ["quick", "Quick", "Matching notes appear as you type."],
  ["standard", "Standard", "An answer with its sources, in seconds."],
  ["deep", "Deep", "An investigation across your history, in a few minutes."],
];

export function RememberView({ route }: { route: Route }) {
  const { api } = useApp();
  // Cue cards are unacknowledged insights (docs/data-model.md): read once here so the other sections
  // do not show the same insight twice. Each cue carries its evidence (why it surfaced).
  const cues = useRead<ListOf<Cue>>(() => api.get<ListOf<Cue>>("note/cues", { limit: 3 }), [api]);
  const insights = useRead<ListOf<Insight>>(() => api.get<ListOf<Insight>>("note/insights", { acked: false, limit: 20 }), [api]);
  const cueIds = useMemo(() => new Set((cues.data?.items || []).map((c) => c.cue_id)), [cues.data]);
  // An empty notebook gets one inviting sentence instead of six empty sections.
  const status = useRead<NoteStatus>(() => api.get<NoteStatus>("note/status"), [api]);
  if (status.data && Number(status.data.counts?.notes ?? 1) === 0) {
    return (
      <div className="nt-center">
        <EmptyNotebook />
      </div>
    );
  }
  return (
    <div className="nt-remember">
      <div className="nt-remember__top">
        <SinceLastLooked hide={cueIds} />
        <CueCards cues={cues} />
      </div>
      <AskPanel initialTopic={route.params.get("topic") || ""} />
      <ThenVersusNow />
      <Revisit insights={insights} hide={cueIds} />
      <ReviewInbox />
    </div>
  );
}

/** "Why this surfaced": the reason from the insight's kind, and the notes it is based on (read when opened). */
function WhySurfaced({ id, kind, evidence }: { id: string; kind: string; evidence: string[] }) {
  const [open, setOpen] = useState(false);
  const panel = `why-${id}`;
  return (
    <div>
      <Disclose open={open} onToggle={() => setOpen(!open)} controls={panel}>
        Why this surfaced
      </Disclose>
      {open ? (
        <div id={panel} className="nt-why">
          <span>
            {whyLine(kind)}
            {evidence.length ? ` ${basisLine(evidence.length)}:` : ""}
          </span>
          {evidence.slice(0, 5).map((nid) => (
            <EvidenceLink key={nid} id={nid} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function EvidenceLink({ id }: { id: string }) {
  const { api } = useApp();
  const note = useRead<Note>(() => api.get<Note>(`note/notes/${encodeURIComponent(id)}`), [api, id]);
  return <NoteLink note={{ note_id: id, title: note.data?.title || (note.loading ? "…" : "A note"), occurred_at: note.data?.occurred_at }} />;
}

// ---------------------------------------------------------------- since you last looked

function SinceLastLooked({ hide }: { hide: Set<string> }) {
  const { api } = useApp();
  // The cursor is opaque (the server's); the moment it was taken is kept beside it for the "Since …" line.
  const [mark, setMark] = useState(() => readMark());
  const [allNotes, setAllNotes] = useState(false);
  const since = mark.cursor;
  const changes = useRead<Changes>(() => api.get<Changes>("note/changes", { since, limit: 50 }), [api, since]);
  const shownInsights = (c: Changes) => (c.insights || []).filter((i) => !hide.has(i.note_id));
  const count = (c: Changes) => (c.notes?.length || 0) + (c.entities?.length || 0) + (c.superseded_claims?.length || 0) + (c.closed_loops?.length || 0) + (c.failed_notes?.length || 0) + shownInsights(c).length;
  return (
    <Section
      id="since"
      title="Since you last looked"
      sub={`since ${longDate(mark.at)}`}
      actions={
        changes.data && count(changes.data) ? (
          <button
            type="button"
            className="nt-btn nt-btn--quiet nt-btn--sm"
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
      <ReadView read={changes} what="what changed" empty={(c) => (count(c) ? false : "Nothing new since then.")}>
        {(c) => (
          <ul className="nt-digest">
            {shownInsights(c).map((i) => (
              <DigestRow key={i.note_id} mark="insight" icon="sparkle" tag={`Insight · ${shortDate(i.occurred_at)}`}>
                <NoteLink note={i} showDate={false} />
              </DigestRow>
            ))}
            {(c.superseded_claims || []).map((cl) => (
              <DigestRow key={cl.claim_id} mark="changed" icon="refresh" tag={cl.kind === "retracted" ? "Withdrawn" : "Replaced"}>
                <button type="button" className="nt-link" onClick={() => openClaim(cl.claim_id)}>
                  <span className="nt-link__title nt-struck">{cl.text}</span>
                </button>
              </DigestRow>
            ))}
            {(c.closed_loops || []).map((t) => (
              <DigestRow key={t.task_id} mark="done" icon="check" tag="Closed">
                <span>{t.text}</span>
              </DigestRow>
            ))}
            {(c.failed_notes || []).map((n) => (
              <DigestRow key={n.note_id} mark="attention" icon="warning" tag="Needs attention">
                <NoteLink note={n} showDate={false} />
                {n.error ? <span className="nt-muted">{`${n.error} Retry it from Settings.`}</span> : null}
              </DigestRow>
            ))}
            {(c.entities || []).length ? (
              <DigestRow mark="new" icon="plus" tag="New">
                <span className="nt-chips">
                  {(c.entities || []).map((e) => (
                    <EntityChip key={e.entity_id} entity={e} />
                  ))}
                </span>
              </DigestRow>
            ) : null}
            {(c.notes || []).length ? (
              <DigestRow mark="new" icon="compose" tag="">
                <Disclose open={allNotes} onToggle={() => setAllNotes(!allNotes)} controls="since-notes">
                  {c.notes!.length === 1 ? "1 new note" : `${c.notes!.length} new notes`}
                </Disclose>
              </DigestRow>
            ) : null}
            {allNotes && (c.notes || []).length ? (
              <li id="since-notes" className="nt-digest__more">
                {(c.notes || []).map((n) => (
                  <NoteLink key={n.note_id} note={n} />
                ))}
              </li>
            ) : null}
          </ul>
        )}
      </ReadView>
    </Section>
  );
}

function DigestRow({ mark, icon, tag, children }: { mark: string; icon: "sparkle" | "refresh" | "check" | "warning" | "plus" | "compose"; tag: string; children: React.ReactNode }) {
  return (
    <li className="nt-digest__row">
      <span className={`nt-digest__mark nt-digest__mark--${mark}`} aria-hidden="true">
        <Icon name={icon} size={14} />
      </span>
      <span className="nt-digest__body">{children}</span>
      <span className="nt-digest__tag">{tag}</span>
    </li>
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

// ---------------------------------------------------------------- cue cards

function CueCards({ cues }: { cues: ReadState<ListOf<Cue>> }) {
  return (
    <Section id="cues" title="Worth a thought">
      <ReadView read={cues} what="your prompts" empty={(c) => (c.items.length ? false : "No prompts right now.")}>
        {(c) => (
          <div className="nt-cues">
            {c.items.map((cue) => (
              <article key={cue.cue_id} className="nt-card nt-cue">
                <p className="nt-cue__text">{cue.text}</p>
                <WhySurfaced id={cue.cue_id} kind={cue.kind} evidence={cue.evidence_note_ids || []} />
                <div className="nt-actions">
                  <button type="button" className="nt-btn nt-btn--primary nt-btn--sm" onClick={() => noteAbout("cue", cue.cue_id, cue.text)}>
                    Answer
                  </button>
                  {cue.subject?.kind === "entity" ? (
                    <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => openEntity(cue.subject.id)}>
                      {`Open ${cue.subject.title}`}
                    </button>
                  ) : null}
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

type Asked = { question: string; effort: Effort; state: "asking" | "answered" | "failed"; output?: RecallOutput; error?: string; steps?: string[] };

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
  // Quick is instant: it follows the typing (a short pause only to spare a request per key).
  const dq = useDebounced(q.trim(), 150);
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
    setAsked({ question, effort, state: "asking", steps: [] });
    try {
      const runId = await startFlow(api, "note-recall", sessionId, { question, effort, as_of: at, since: filters.since ?? null, until: filters.until ?? null, entities: filters.entities, topics });
      const run = await followRun(api, runId, {
        onStep: (record) => {
          const words = stepWords(record);
          if (!words || !alive.current) return;
          setAsked((prev) => (prev && prev.state === "asking" && !(prev.steps || []).includes(words) ? { ...prev, steps: [...(prev.steps || []), words] } : prev));
        },
      });
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
          {effort === "quick" ? null : (
            <button type="submit" className="nt-btn nt-btn--primary" disabled={!q.trim() || asked?.state === "asking"}>
              {asked?.state === "asking" ? "Asking…" : "Ask"}
            </button>
          )}
        </div>
        <div className="nt-ask__row">
          <div className="nt-segmented" role="radiogroup" aria-label="Effort">
            {EFFORTS.map(([v, label, hint]) => (
              <button key={v} type="button" role="radio" aria-checked={effort === v} className="nt-segmented__opt" title={hint} onClick={() => setEffort(v)}>
                {label}
              </button>
            ))}
          </div>
          <span className="nt-effort__hint">{EFFORTS.find(([v]) => v === effort)![2]}</span>
        </div>
        <div className="nt-chips" aria-label="Narrow down">
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
      {/* Once an answer to this very question is shown, its originals replace the matching notes. */}
      {(quick.data || quick.loading) && !(asked && asked.state === "answered" && asked.question === q.trim()) ? (
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

function LayerLabel({ icon, name, what }: { icon: "sparkle" | "file" | "branch"; name: string; what: string }) {
  return (
    <p className="nt-layer__label">
      <Icon name={icon} size={12} />
      <span>{name}</span>
      <span>{what}</span>
    </p>
  );
}

/** An answer in three layers (deck slide 23): the synthesis, the originals it rests on, and what the notebook understood. */
function AnswerCard({ asked, onSave, saved, onFollowUp }: { asked: Asked; onSave: () => void; saved: string; onFollowUp: (q: string) => void }) {
  if (asked.state === "asking") {
    const steps = asked.steps || [];
    return (
      <div className="nt-answer" role="status" aria-live="polite">
        <section className="nt-layer nt-layer--synthesis">
          <LayerLabel icon="sparkle" name="Synthesis" what={asked.effort === "deep" ? "investigating across your history" : "working it out"} />
          <ol className="nt-progress">
            {steps.map((step) => (
              <li key={step}>
                <Icon name="check" size={14} className="nt-progress__done" />
                <span>{step}</span>
              </li>
            ))}
            <li>
              <Icon name="loader" size={14} className="nt-spin" />
              <span>{steps.length ? "Still working…" : "Starting…"}</span>
            </li>
          </ol>
          {asked.effort === "deep" ? <p className="nt-muted">This takes a few minutes; it also appears under Revisit when it is ready.</p> : null}
        </section>
      </div>
    );
  }
  if (asked.state === "failed") {
    return (
      <div className="nt-answer" role="alert">
        <p className="nt-error-text">{asked.error}</p>
      </div>
    );
  }
  const out = asked.output || {};
  const evolution = thenNowView(out.evolution);
  const text = out.answer || out.response || "";
  const { segments, cited } = splitCitations(text);
  const evidence = out.evidence || [];
  const titleOf = (id: string) => evidence.find((e) => e.note_id === id)?.title || "a note";
  // The originals: the cited notes first (in citation order), then the rest of the evidence.
  const order = [...cited, ...evidence.map((e) => e.note_id).filter((id) => !cited.includes(id))];
  const loops = out.open_loops || [];
  return (
    <article className="nt-answer" aria-label="Answer">
      <section className="nt-layer nt-layer--synthesis">
        <LayerLabel icon="sparkle" name="Synthesis" what="what your notes suggest" />
        <p className="nt-answer-text">
          {segments.map((sg, i) =>
            "cite" in sg ? (
              <button key={i} type="button" className="nt-cite" title={`Open “${titleOf(sg.noteId)}”`} aria-label={`Source ${sg.cite}: ${titleOf(sg.noteId)}`} onClick={() => openNote(sg.noteId)}>
                {sg.cite}
              </button>
            ) : (
              <span key={i}>{sg.text}</span>
            ),
          )}
        </p>
        {(out.follow_ups || []).length ? (
          <div className="nt-chips nt-block">
            {(out.follow_ups || []).map((f) => (
              <AfChipButton key={f} size="sm" onClick={() => onFollowUp(f)}>
                {f}
              </AfChipButton>
            ))}
          </div>
        ) : null}
      </section>
      {order.length ? (
        <section className="nt-layer nt-layer--original">
          <LayerLabel icon="file" name="Original" what="what you wrote" />
          <ol className="nt-original">
            {order.map((id) => {
              const ev = evidence.find((e) => e.note_id === id);
              return (
                <li key={id}>
                  <span className="nt-chips">
                    <NoteLink note={{ note_id: id, title: ev?.title || "A note", occurred_at: ev?.occurred_at }} />
                    {ev?.kind ? <KindBadge kind={ev.kind} /> : null}
                  </span>
                  {ev?.excerpt ? <blockquote className="nt-original__quote">{ev.excerpt}</blockquote> : null}
                </li>
              );
            })}
          </ol>
        </section>
      ) : null}
      {evolution || loops.length ? (
        <section className="nt-layer nt-layer--interpretation">
          <LayerLabel icon="branch" name="Interpretation" what="what your notebook understood" />
          {evolution ? <ThenNowCard data={evolution} title="How it changed" /> : null}
          {loops.length ? (
            <div className={evolution ? "nt-block" : undefined}>
              <h3>Still open</h3>
              <ul className="nt-list">
                {loops.map((t) => (
                  <li key={t.task_id}>{t.text}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
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
  const candidates = uniqueEntities(overview.data?.entities);
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
                  {uniqueEntities(o.entities).map((e) => (
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
                  {uniqueEntities(o.entities)
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

function Revisit({ insights, hide }: { insights: ReadState<ListOf<Insight>>; hide: Set<string> }) {
  const { api } = useApp();
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
        <div>
          <h3>Insights</h3>
          {/* The prompts above are insights too: each shows once. */}
          <ReadView read={insights} what="your insights" empty={(l) => (l.items.some((i) => !hide.has(i.note_id)) ? false : "Nothing else to read.")}>
            {(l) => (
              <ul className="nt-list">
                {l.items
                  .filter((i) => !hide.has(i.note_id))
                  .map((i) => (
                    <li key={i.note_id} className="nt-item">
                      <p className="nt-item__title">{i.title}</p>
                      {i.body ? <p className="nt-muted">{i.body}</p> : null}
                      <WhySurfaced id={i.note_id} kind={i.insight_kind} evidence={i.evidence_note_ids || []} />
                      <div className="nt-actions">
                        <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => openNote(i.note_id)}>
                          Open
                        </button>
                        <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => noteAbout("insight", i.note_id, i.title)}>
                          Add a note
                        </button>
                        <button type="button" className="nt-btn nt-btn--sm" disabled={busy === i.note_id} onClick={() => void act(i.note_id, `note/insights/${encodeURIComponent(i.note_id)}/ack`, "Marked as read.", insights.reload)}>
                          {busy === i.note_id ? "Saving…" : "Got it"}
                        </button>
                      </div>
                    </li>
                  ))}
              </ul>
            )}
          </ReadView>
        </div>
        <div>
          <h3>Open loops</h3>
          <ReadView read={tasks} what="your open loops" empty={(l) => (l.items.length ? false : "No open loops.")}>
            {(l) => (
              <ul className="nt-list">
                {l.items.map((t) => (
                  <li key={t.task_id} className="nt-item">
                    <p className="nt-item__title">{t.text}</p>
                    {t.due ? <p className="nt-muted">Due {longDate(t.due)}</p> : null}
                    <div className="nt-actions">
                      <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => openNote(t.note_id)}>
                        Open
                      </button>
                      <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => noteAbout("task", t.task_id, t.text)}>
                        Add a note
                      </button>
                      <button type="button" className="nt-btn nt-btn--sm" disabled={busy === t.task_id} onClick={() => void act(t.task_id, `note/tasks/${encodeURIComponent(t.task_id)}/close`, `Closed: ${t.text}.`, tasks.reload, {})}>
                        {busy === t.task_id ? "Closing…" : "Close"}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </ReadView>
        </div>
        <div>
          <h3>Worth another look</h3>
          <ReadView read={overview} what="this" empty={(o) => ((o.resurfacing || []).length ? false : "Nothing to resurface today.")}>
            {(o) => (
              <ul className="nt-list">
                {(o.resurfacing || []).map((n) => (
                  <li key={n.note_id} className="nt-item">
                    <NoteLink note={n} />
                    {n.excerpt ? <p className="nt-muted">{n.excerpt}</p> : null}
                    <div className="nt-actions">
                      <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => noteAbout("note", n.note_id, n.title)}>
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

// ---------------------------------------------------------------- review

/**
 * What the notebook proposed or did on its own and you may want to check (GET /note/review): close matches,
 * merges it made, disagreements it kept. Each item brings its own actions. Shown only when there is one.
 */
function ReviewInbox() {
  const { api } = useApp();
  const review = useRead<ListOf<ReviewItem>>(() => fetchReview(api), [api]);
  const [line, setLine] = useState("");
  const items = review.data?.items || [];
  if (!items.length && !line) return null;
  return (
    <Section id="review" title="To review" sub="what your notebook decided on its own">
      {line ? (
        <p className="nt-muted" role="status">
          {line}
        </p>
      ) : null}
      <ul className="nt-list nt-review nt-revisit">
        {items.map((it) => (
          <ReviewCard
            key={it.review_id}
            item={it}
            onDone={(words) => {
              setLine(words);
              review.reload();
            }}
          />
        ))}
      </ul>
    </Section>
  );
}

function SideChip({ side }: { side: ReviewSide }) {
  if (side.type === "entity") return <EntityChip entity={{ entity_id: side.id, name: side.name, kind: side.kind || "thing" }} />;
  return (
    <button type="button" className="nt-link" onClick={() => openClaim(side.id)}>
      <span className="nt-link__title">{side.name}</span>
    </button>
  );
}

type Pending = { action: ReviewAction; question: string; verb: string; done: string; reason?: string; danger?: boolean } | null;

function ReviewCard({ item, onDone }: { item: ReviewItem; onDone: (words: string) => void }) {
  const { api } = useApp();
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [a, b] = item.sides;
  // A merge's first side is the entry that was joined in: its own record (as_merged) says what it was.
  const own = useRead<EntityRecord | null>(() => (item.kind === "merge" && a?.type === "entity" ? api.get<EntityRecord>(`note/entities/${encodeURIComponent(a.id)}`, { as_merged: true }) : Promise.resolve(null)), [api, item.kind, a?.id]);
  const run = async (p: NonNullable<Pending>) => {
    setBusy(true);
    setError("");
    try {
      await runReviewAction(api, p.action, p.reason);
      setPending(null);
      onDone(p.done);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 401 ? "Please sign in again." : "That did not work; try again.");
    } finally {
      setBusy(false);
    }
  };
  const confirm = reviewAction(item, "confirm");
  const unmerge = reviewAction(item, "unmerge");
  const apart = reviewAction(item, "keep_apart");
  const retracts = item.actions.filter((x) => x.action === "retract");
  const head =
    item.kind === "merge" ? "Joined" : item.kind === "close_match" ? "Maybe the same" : "These disagree";
  return (
    <li className="nt-item" data-kind={item.kind}>
      <p className="nt-review__pair">
        <span>{head}</span>
        {a ? <SideChip side={a} /> : null}
        <span className="nt-muted">{item.kind === "merge" ? "into" : item.kind === "close_match" ? "and" : "versus"}</span>
        {b ? <SideChip side={b} /> : null}
        <span className="nt-link__date">{shortDate(item.at)}</span>
      </p>
      <p className="nt-muted">{reviewWhy(item.why)}</p>
      {own.data ? (
        <p className="nt-muted">{`${own.data.name} on its own: ${own.data.note_count === 1 ? "1 note" : `${own.data.note_count} notes`}${own.data.summary ? `; ${own.data.summary}` : ""}`}</p>
      ) : null}
      {item.reason ? <p className="nt-muted">{`Why: ${item.reason}`}</p> : null}
      {item.evidence_note_ids.length ? (
        <div className="nt-chips">
          {item.evidence_note_ids.slice(0, 3).map((id) => (
            <EvidenceLink key={id} id={id} />
          ))}
        </div>
      ) : null}
      {error ? <p className="nt-error-text">{error}</p> : null}
      {pending ? (
        <div className="nt-confirm" role="group" aria-label="Confirm">
          <span>{pending.question}</span>
          <button type="button" className="nt-btn nt-btn--sm" onClick={() => setPending(null)} disabled={busy}>
            Not now
          </button>
          <button type="button" className={`nt-btn nt-btn--sm${pending.danger ? " nt-btn--danger" : ""}`} onClick={() => void run(pending)} disabled={busy}>
            {busy ? "Saving…" : pending.verb}
          </button>
        </div>
      ) : (
        <div className="nt-actions">
          {item.kind === "merge" && confirm ? (
            <button type="button" className="nt-btn nt-btn--sm" disabled={busy} onClick={() => void run({ action: confirm, question: "", verb: "", done: "Kept. They stay joined." })}>
              Keep
            </button>
          ) : null}
          {item.kind === "merge" && unmerge ? (
            <button type="button" className="nt-btn nt-btn--sm" onClick={() => setPending({ action: unmerge, question: "Undo this match? The two entries become separate again; your notes stay.", verb: "Undo", done: "Undone. They are separate again.", danger: true })}>
              Undo the match
            </button>
          ) : null}
          {item.kind === "close_match" && confirm ? (
            <button type="button" className="nt-btn nt-btn--sm" onClick={() => setPending({ action: confirm, question: `Join ${a?.name} into ${b?.name}? Their notes and links come together.`, verb: "Join them", done: "Joined." })}>
              Same
            </button>
          ) : null}
          {item.kind === "close_match" && apart ? (
            <button type="button" className="nt-btn nt-btn--sm" disabled={busy} onClick={() => void run({ action: apart, question: "", verb: "", done: "Kept apart.", reason: "Kept apart in the web app." })}>
              Not the same
            </button>
          ) : null}
          {item.kind === "contradiction"
            ? retracts.map((r, i) => (
                <button key={r.path} type="button" className="nt-btn nt-btn--sm" onClick={() => setPending({ action: r, question: `Withdraw "${item.sides[i]?.name}"? The note stays.`, verb: "Withdraw", done: "Withdrawn.", reason: "Withdrawn while reviewing a disagreement in the web app.", danger: true })}>
                  {`${item.sides[i]?.name} is wrong`}
                </button>
              ))
            : null}
        </div>
      )}
    </li>
  );
}
