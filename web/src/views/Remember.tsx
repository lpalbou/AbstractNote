import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AfChip, AfChipButton, Icon } from "@abstractframework/ui-kit";
import { ApiError, errorSentence, type Api } from "../lib/api";
import { splitCitations } from "../lib/citations";
import { useDebounced, useRead, type ReadState } from "../lib/hooks";
import { CURSOR_KEY, readLocal, writeLocal } from "../lib/local";
import { navigate, noteAbout, useAt, type Route } from "../lib/nav";
import { PANELS, THOUGHT_GROUPS, ageWords, changeCount, groupThoughts, keyAction, panelOf, type PanelId, type Thought } from "../lib/remember";
import { fetchReview, reviewAction, runReviewAction } from "../lib/review";
import { followRun, runErrorText, startFlow } from "../lib/runs";
import type { Changes, Communities, EntityListItem, EntityCard, EntityRecord, Insight, ListOf, Note, NoteRef, NoteStatus, Overview, RecallOutput, RelationTimeline, ReviewAction, ReviewItem, ReviewSide, SearchResult, Task, ThenNowRaw, Topic } from "../lib/types";
import { claimsById, thenNowView, uniqueEntities } from "../lib/shapes";
import { NOTE_KINDS, claimSentence, longDate, relative, shortDate, topicLabel } from "../lib/words";
import { basisLine, reviewWhy, whyLine } from "../lib/why";
import { Disclose, EmptyNotebook, EntityChip, KindBadge, NoteLink, ReadView, Sparkline, openClaim, openEntity, openNote, useApp } from "../components/common";
import { ThenNowCard } from "../components/ThenNow";
import { TimeScrubber } from "../components/TimeScrubber";
import { ThemeCards } from "./Overview";

type Effort = "quick" | "standard" | "deep";
const EFFORTS: Array<[Effort, string, string]> = [
  ["quick", "Quick", "Matching notes appear as you type."],
  ["standard", "Standard", "An answer with its sources, in seconds."],
  ["deep", "Deep", "An investigation across your history, in a few minutes."],
];

/**
 * Remember (operator, 2026-10-19): one screen, no page scroll. A rail of seven panels by what you want to
 * remember or how (What changed, Ask, Worth a thought, Then and now, Open loops, To review, Themes), one shown
 * at a time and scrolling inside itself, the ask bar always at the top. Keys: 1–7 choose a panel, "/" the
 * question. On a phone the rail is a sideways row at the top.
 */
export function RememberView({ route }: { route: Route }) {
  const { api } = useApp();
  const topic = route.params.get("topic") || "";
  const panel = panelOf(route.params.get("panel") || (topic ? "ask" : null));
  const go = (p: PanelId) => navigate("remember", { panel: p, topic: topic || null });

  // Everything the badges count is read once here and handed to its panel.
  const [mark, setMark] = useState(() => readMark());
  const changes = useRead<Changes>(() => api.get<Changes>("note/changes", { since: mark.cursor, limit: 50 }), [api, mark.cursor]);
  const insights = useRead<ListOf<Insight>>(() => api.get<ListOf<Insight>>("note/insights", { acked: false, limit: 50 }), [api]);
  const review = useRead<ListOf<ReviewItem>>(() => fetchReview(api), [api]);
  const tasks = useRead<ListOf<Task>>(() => api.get<ListOf<Task>>("note/tasks", { status: "open", limit: 50 }), [api]);
  const overview = useRead<Overview>(() => api.get<Overview>("note/overview", { days: 30 }), [api]);
  const communities = useRead<Communities>(() => api.get<Communities>("note/communities"), [api]);
  const status = useRead<NoteStatus>(() => api.get<NoteStatus>("note/status"), [api]);
  const ask = useAsk(topic);
  const askInput = useRef<HTMLInputElement>(null);

  const thoughts = useMemo(() => groupThoughts(insights.data?.items || [], review.data?.items || [], overview.data?.resurfacing || []), [insights.data, review.data, overview.data]);
  const badges: Partial<Record<PanelId, number>> = {
    changed: changeCount(changes.data),
    thoughts: thoughts.reduce((a, g) => a + g.items.length, 0),
    loops: tasks.data?.items.length || 0,
    review: (review.data?.items || []).filter((r) => r.kind !== "contradiction").length,
    themes: communities.data?.items.length || 0,
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const a = keyAction(e.key, e.target as HTMLElement, { ctrl: e.ctrlKey, meta: e.metaKey, alt: e.altKey });
      if (!a) return;
      e.preventDefault();
      if ("focusAsk" in a) askInput.current?.focus();
      else go(a.panel);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topic]);

  // An empty notebook gets one inviting sentence instead of seven empty panels.
  if (status.data && Number(status.data.counts?.notes ?? 1) === 0) {
    return (
      <div className="nt-center">
        <EmptyNotebook />
      </div>
    );
  }
  const meta = PANELS.find((p) => p.id === panel)!;
  return <RememberLayout panel={panel} meta={meta} badges={badges} go={go} ask={ask} askInput={askInput} mark={mark} setMark={setMark} changes={changes} insights={insights} review={review} overview={overview} tasks={tasks} communities={communities} thoughts={thoughts} />;
}

function RememberLayout({ panel, meta, badges, go, ask, askInput, mark, setMark, changes, insights, review, overview, tasks, communities, thoughts }: { panel: PanelId; meta: (typeof PANELS)[number]; badges: Partial<Record<PanelId, number>>; go: (p: PanelId) => void; ask: AskState; askInput: React.RefObject<HTMLInputElement>; mark: { cursor: string; at: string }; setMark: (m: { cursor: string; at: string }) => void; changes: ReadState<Changes>; insights: ReadState<ListOf<Insight>>; review: ReadState<ListOf<ReviewItem>>; overview: ReadState<Overview>; tasks: ReadState<ListOf<Task>>; communities: ReadState<Communities>; thoughts: ReturnType<typeof groupThoughts> }) {
  // On a phone the rail scrolls sideways: the chosen panel is brought into view.
  const rail = useRef<HTMLUListElement>(null);
  useEffect(() => {
    const box = rail.current;
    const on = box?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (box && on && box.scrollWidth > box.clientWidth + 1) box.scrollLeft = Math.max(0, on.offsetLeft - 24);
  }, [panel]);
  return (
    <div className="nt-rem">
      <nav className="nt-rem__rail" aria-label="Remember">
        <ul ref={rail} role="tablist" aria-orientation="vertical">
          {PANELS.map((p, i) => {
            const n = badges[p.id];
            return (
              <li key={p.id} role="presentation">
                <button type="button" role="tab" id={`rem-tab-${p.id}`} aria-selected={panel === p.id} aria-controls="rem-panel" className="nt-rem__tab" onClick={() => go(p.id)} title={`${p.label} (${i + 1})`}>
                  <Icon name={p.icon} size={16} />
                  <span className="nt-rem__tab-label">{p.label}</span>
                  {n ? (
                    <span className="nt-rem__badge" aria-label={`${n} to see`} data-count={n}>
                      {n > 99 ? "99+" : n}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
        <p className="nt-rem__keys">
          <kbd>1</kbd>–<kbd>7</kbd> switch · <kbd>/</kbd> ask
        </p>
      </nav>
      <div className="nt-rem__content">
        <AskBar ask={ask} inputRef={askInput} onTyping={() => panel !== "ask" && go("ask")} onAsk={() => go("ask")} />
        <section id="rem-panel" className="nt-rem__panel" role="tabpanel" aria-labelledby={`rem-tab-${panel}`} data-panel={panel}>
          <header className="nt-rem__head">
            <h2>
              {meta.label}
              <span className="nt-section__sub">{panel === "changed" ? `since ${longDate(mark.at)}` : meta.sub}</span>
            </h2>
            {panel === "changed" && changeCount(changes.data) ? (
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
            ) : null}
          </header>
          <div className="nt-rem__body">
            {panel === "changed" ? <ChangedPanel changes={changes} onThoughts={() => go("thoughts")} /> : null}
            {panel === "ask" ? <AskPanel ask={ask} /> : null}
            {panel === "thoughts" ? <ThoughtsPanel read={[insights, review, overview]} groups={thoughts} onChanged={() => (insights.reload(), review.reload())} /> : null}
            {panel === "thennow" ? <ThenVersusNow /> : null}
            {panel === "loops" ? <LoopsPanel tasks={tasks} /> : null}
            {panel === "review" ? <ReviewPanel review={review} /> : null}
            {panel === "themes" ? <ThemeCards communities={communities} onTheme={(id) => navigate("graph", { view: "picture", community: id })} /> : null}
          </div>
        </section>
      </div>
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

// ---------------------------------------------------------------- what changed

type RowIcon = "sparkle" | "refresh" | "check" | "warning" | "plus" | "compose";

/** One row per item, each with one action. */
function ChangedPanel({ changes, onThoughts }: { changes: ReadState<Changes>; onThoughts: () => void }) {
  return (
    <ReadView read={changes} what="what changed" empty={(c) => (changeCount(c) ? false : "Nothing new since then.")}>
      {(c) => (
        <ul className="nt-rows">
          {(c.insights || []).map((i) => (
            <ChangeRow key={i.note_id} mark="insight" icon="sparkle" tag="Insight" when={i.occurred_at} action={["See it", onThoughts]}>
              {i.title}
            </ChangeRow>
          ))}
          {(c.superseded_claims || []).map((cl) => (
            <ChangeRow key={cl.claim_id} mark="changed" icon="refresh" tag={cl.kind === "retracted" ? "Withdrawn" : "Replaced"} action={["Why", () => openClaim(cl.claim_id)]}>
              <span className="nt-struck">{cl.text}</span>
            </ChangeRow>
          ))}
          {(c.closed_loops || []).map((t) => (
            <ChangeRow key={t.task_id} mark="done" icon="check" tag="Closed" when={t.closed_at} action={["Add a note", () => noteAbout("task", t.task_id, t.text)]}>
              {t.text}
            </ChangeRow>
          ))}
          {(c.failed_notes || []).map((n) => (
            <ChangeRow key={n.note_id} mark="attention" icon="warning" tag="Needs attention" action={["Open", () => openNote(n.note_id)]}>
              {n.error ? `${n.title}: ${n.error}` : n.title}
            </ChangeRow>
          ))}
          {(c.entities || []).map((e) => (
            <ChangeRow key={e.entity_id} mark="new" icon="plus" tag={`New ${e.kind}`} when={e.first_seen} action={["Open", () => openEntity(e.entity_id)]}>
              {e.name}
            </ChangeRow>
          ))}
          {(c.notes || []).map((n) => (
            <ChangeRow key={n.note_id} mark="note" icon="compose" tag={n.kind} when={n.occurred_at} action={["Open", () => openNote(n.note_id)]}>
              {n.title}
            </ChangeRow>
          ))}
        </ul>
      )}
    </ReadView>
  );
}

function ChangeRow({ mark, icon, tag, when, action, children }: { mark: string; icon: RowIcon; tag: string; when?: string | null; action: [string, () => void]; children: React.ReactNode }) {
  return (
    <li className="nt-row">
      <span className={`nt-digest__mark nt-digest__mark--${mark}`} aria-hidden="true">
        <Icon name={icon} size={14} />
      </span>
      <span className="nt-row__text">{children}</span>
      <span className="nt-row__tag">{when ? `${tag} · ${shortDate(when)}` : tag}</span>
      <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm nt-row__act" onClick={action[1]}>
        {action[0]}
      </button>
    </li>
  );
}

// ---------------------------------------------------------------- worth a thought

/** A compact card (deck slides 18, 26): a kind pill, a one-line title, two lines of body with "More", why it
 * surfaced on demand, two actions at most. */
function ThoughtCard({ pill, title, onTitle, body, when, why, actions, children }: { pill: string; title: string; onTitle?: () => void; body?: string; when?: string; why?: React.ReactNode; actions: React.ReactNode; children?: React.ReactNode }) {
  const [more, setMore] = useState(false);
  const [long, setLong] = useState(false);
  const ref = useRef<HTMLParagraphElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !more) setLong(el.scrollHeight > el.clientHeight + 1);
  }, [body, more]);
  return (
    <article className="nt-thought">
      <div className="nt-thought__top">
        <span className="nt-pill">{pill}</span>
        {when ? <span className="nt-link__date">{shortDate(when)}</span> : null}
      </div>
      {onTitle ? (
        <button type="button" className="nt-thought__title nt-thought__title--link" onClick={onTitle} title={title}>
          {title}
        </button>
      ) : (
        <p className="nt-thought__title" title={title}>
          {title}
        </p>
      )}
      {body ? (
        <p ref={ref} className={`nt-thought__body${more ? " nt-thought__body--open" : ""}`}>
          {body}
        </p>
      ) : null}
      {body && (long || more) ? (
        <button type="button" className="nt-disclose" aria-expanded={more} onClick={() => setMore(!more)}>
          {more ? "Less" : "More"}
        </button>
      ) : null}
      {children}
      {why}
      <div className="nt-actions nt-thought__actions">{actions}</div>
    </article>
  );
}

function ThoughtsPanel({ read, groups, onChanged }: { read: [ReadState<ListOf<Insight>>, ReadState<ListOf<ReviewItem>>, ReadState<Overview>]; groups: ReturnType<typeof groupThoughts>; onChanged: () => void }) {
  const [line, setLine] = useState("");
  const [insights] = read;
  if (insights.data === null) return <ReadView read={insights} what="your insights">{() => null}</ReadView>;
  if (!groups.length) return <p className="nt-muted">Nothing to think about right now.</p>;
  return (
    <>
      {line ? (
        <p className="nt-muted" role="status">
          {line}
        </p>
      ) : null}
      {groups.map(({ group, items }) => (
        <section key={group.id} className="nt-group" aria-labelledby={`thoughts-${group.id}`}>
          <h3 id={`thoughts-${group.id}`} className="nt-group__head">
            {group.label}
            <span className="nt-group__count">{items.length}</span>
          </h3>
          <div className="nt-thoughts">
            {items.map((t) => (
              <ThoughtItem key={t.id} t={t} pill={THOUGHT_GROUPS.find((g) => g.id === t.group)!.pill} onDone={(w) => (setLine(w), onChanged())} />
            ))}
          </div>
        </section>
      ))}
    </>
  );
}

function ThoughtItem({ t, pill, onDone }: { t: Thought; pill: string; onDone: (words: string) => void }) {
  const { api } = useApp();
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<{ action: ReviewAction; name: string } | null>(null);
  const post = async (fn: () => Promise<unknown>, words: string) => {
    setBusy(true);
    try {
      await fn();
      onDone(words);
    } catch (e) {
      onDone(errorSentence(e, "that").replace("could not be loaded", "did not work"));
    } finally {
      setBusy(false);
    }
  };
  if (t.type === "insight") {
    const i = t.insight;
    return (
      <ThoughtCard
        pill={pill}
        title={i.title}
        onTitle={() => openNote(i.note_id)}
        body={i.body}
        when={i.occurred_at}
        why={<WhySurfaced id={i.note_id} kind={i.insight_kind} evidence={i.evidence_note_ids || []} />}
        actions={
          <>
            <button type="button" className="nt-btn nt-btn--primary nt-btn--sm" onClick={() => noteAbout("insight", i.note_id, i.title)}>
              {t.group === "questions" ? "Answer" : "Add a note"}
            </button>
            <button type="button" className="nt-btn nt-btn--sm" disabled={busy} onClick={() => void post(() => api.post(`note/insights/${encodeURIComponent(i.note_id)}/ack`), "Marked as read.")}>
              {busy ? "Saving…" : "Got it"}
            </button>
          </>
        }
      />
    );
  }
  if (t.type === "resurface") {
    const n = t.note;
    return (
      <ThoughtCard
        pill={pill}
        title={n.title}
        onTitle={() => openNote(n.note_id)}
        body={n.excerpt}
        when={n.occurred_at}
        why={<WhyLines id={n.note_id} line="An older note close to what you write now: worth another look." evidence={[]} />}
        actions={
          <>
            <button type="button" className="nt-btn nt-btn--primary nt-btn--sm" onClick={() => noteAbout("note", n.note_id, n.title)}>
              Add a note
            </button>
            <button type="button" className="nt-btn nt-btn--sm" onClick={() => openNote(n.note_id)}>
              Open
            </button>
          </>
        }
      />
    );
  }
  // A tension: two statements the notebook kept side by side; settle it by withdrawing one.
  const r = t.item;
  const retracts = r.actions.filter((x) => x.action === "retract");
  return (
    <ThoughtCard
      pill={pill}
      title="Two of your notes disagree"
      when={r.at}
      why={
        <WhyLines id={r.review_id} line={reviewWhy(r.why)} evidence={r.evidence_note_ids} />
      }
      actions={
        confirm ? (
          <span className="nt-confirm" role="group" aria-label="Confirm">
            <span>{`Withdraw "${confirm.name}"? The note stays.`}</span>
            <button type="button" className="nt-btn nt-btn--sm" onClick={() => setConfirm(null)} disabled={busy}>
              Not now
            </button>
            <button type="button" className="nt-btn nt-btn--sm nt-btn--danger" disabled={busy} onClick={() => void post(() => runReviewAction(api, confirm.action, "Withdrawn while reviewing a disagreement in the web app."), "Withdrawn.")}>
              {busy ? "Saving…" : "Withdraw"}
            </button>
          </span>
        ) : (
          <>
            {retracts.slice(0, 2).map((x, k) => (
              <button key={x.path} type="button" className="nt-btn nt-btn--sm" onClick={() => setConfirm({ action: x, name: r.sides[k]?.name || "this" })}>
                {k === 0 ? "The first is wrong" : "The second is wrong"}
              </button>
            ))}
          </>
        )
      }
    >
      <ol className="nt-tension">
        {r.sides.slice(0, 2).map((s) => (
          <li key={s.id}>
            <SideChip side={s} />
          </li>
        ))}
      </ol>
    </ThoughtCard>
  );
}

/** "Why this surfaced" for a review item: its rule in words and the notes behind it. */
function WhyLines({ id, line, evidence }: { id: string; line: string; evidence: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <Disclose open={open} onToggle={() => setOpen(!open)} controls={`why-${id}`}>
        Why this surfaced
      </Disclose>
      {open ? (
        <div id={`why-${id}`} className="nt-why">
          <span>{line}</span>
          {evidence.slice(0, 3).map((nid) => (
            <EvidenceLink key={nid} id={nid} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- open loops

function LoopsPanel({ tasks }: { tasks: ReadState<ListOf<Task>> }) {
  const { api } = useApp();
  const [busy, setBusy] = useState("");
  const [line, setLine] = useState("");
  const close = async (t: Task) => {
    setBusy(t.task_id);
    setLine("");
    try {
      await api.post(`note/tasks/${encodeURIComponent(t.task_id)}/close`, {});
      setLine(`Closed: ${t.text}.`);
      tasks.reload();
    } catch (e) {
      setLine(errorSentence(e, "that").replace("could not be loaded", "did not work"));
    } finally {
      setBusy("");
    }
  };
  return (
    <>
      {line ? (
        <p className="nt-muted" role="status">
          {line}
        </p>
      ) : null}
      <ReadView read={tasks} what="your open loops" empty={(l) => (l.items.length ? false : "No open loops.")}>
        {(l) => (
          <ul className="nt-rows">
            {[...l.items].sort((a, b) => (b.age_days ?? 0) - (a.age_days ?? 0)).map((t) => (
              <li key={t.task_id} className={`nt-row nt-row--loop${t.stale ? " nt-row--stale" : ""}`}>
                <span className="nt-digest__mark" aria-hidden="true">
                  <Icon name="loop" size={14} />
                </span>
                <button type="button" className="nt-row__text nt-row__text--link" onClick={() => openNote(t.note_id)} title={t.text}>
                  {t.text}
                </button>
                <span className="nt-row__tag">{`${ageWords(t.age_days)}${t.stale ? " · still relevant?" : ""}${t.due ? ` · due ${shortDate(t.due)}` : ""}`}</span>
                <span className="nt-row__acts">
                  <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => noteAbout("task", t.task_id, t.text)}>
                    Add a note
                  </button>
                  <button type="button" className="nt-btn nt-btn--sm" disabled={busy === t.task_id} onClick={() => void close(t)}>
                    {busy === t.task_id ? "Closing…" : "Close"}
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </ReadView>
    </>
  );
}

// ---------------------------------------------------------------- to review

/** Close matches and merges the notebook made on its own (disagreements are tensions in "Worth a thought"). */
function ReviewPanel({ review }: { review: ReadState<ListOf<ReviewItem>> }) {
  const [line, setLine] = useState("");
  return (
    <>
      {line ? (
        <p className="nt-muted" role="status">
          {line}
        </p>
      ) : null}
      <ReadView read={review} what="what to review" empty={(r) => (r.items.some((i) => i.kind !== "contradiction") ? false : "Nothing to review.")}>
        {(r) => (
          <ul className="nt-list nt-review nt-thoughts">
            {r.items
              .filter((i) => i.kind !== "contradiction")
              .map((it) => (
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
        )}
      </ReadView>
    </>
  );
}

// ---------------------------------------------------------------- ask

type Asked = { question: string; effort: Effort; state: "asking" | "answered" | "failed"; output?: RecallOutput; error?: string; steps?: string[] };
type AskState = ReturnType<typeof useAsk>;

/** The question and its answer live above the panels, so switching panels never loses them. */
function useAsk(initialTopic: string) {
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
  // Closes the run's live view when the page goes away (the answer would have nowhere to go).
  const leaving = useRef<AbortController>(new AbortController());
  useEffect(() => {
    alive.current = true;
    leaving.current = new AbortController();
    return () => {
      alive.current = false;
      leaving.current.abort();
    };
  }, []);
  useEffect(() => {
    if (initialTopic) setTopics([initialTopic]);
  }, [initialTopic]);

  const filters = { since: since ? `${since}T00:00:00Z` : undefined, until: until ? `${until}T23:59:59Z` : at || undefined, kinds, topics, entities: entities.map((e) => e.entity_id) };
  const hasFilter = Boolean(since || until || kinds.length || topics.length || entities.length);
  // Quick recall needs words (GET /note/search requires q); cue chips alone list the matching notes.
  const quick = useRead<SearchResult | null>(() => (dq ? api.get<SearchResult>("note/search", { q: dq, ...filters, limit: 12 }) : hasFilter ? listNotes(api) : Promise.resolve(null)), [api, dq, since, until, at, kinds.join(), topics.join(), entities.map((e) => e.entity_id).join()]);

  async function listNotes(a: Api): Promise<SearchResult> {
    const page = await a.get<ListOf<NoteRef>>("note/notes", { since: filters.since, until: filters.until, kind: kinds[0], topic: topics[0], entity: filters.entities[0], limit: 12 });
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
        signal: leaving.current.signal,
        onProgress: (words) => {
          if (!alive.current) return;
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
      const run = await followRun(api, runId, { signal: leaving.current.signal });
      const refused = runErrorText(run);
      setSaved(refused === null ? "Saved as a note." : run.status === "waiting" ? refused : "The answer could not be saved.");
    } catch {
      setSaved("The answer could not be saved.");
    }
  };
  return { at, setAt, q, setQ, effort, setEffort, since, setSince, until, setUntil, kinds, setKinds, topics, setTopics, entities, setEntities, open, setOpen, asked, saved, quick, ask, saveAnswer };
}

/** The question, always at the top of the panels: typing opens Ask; Enter asks (Quick needs no button). */
function AskBar({ ask, inputRef, onTyping, onAsk }: { ask: AskState; inputRef: React.RefObject<HTMLInputElement>; onTyping: () => void; onAsk: () => void }) {
  return (
    <form
      className="nt-rem__ask"
      onSubmit={(e) => {
        e.preventDefault();
        onAsk();
        void ask.ask();
      }}
    >
      <div className="nt-ask__bar">
        <Icon name="history" size={18} />
        <input
          ref={inputRef}
          className="nt-input nt-input--bare"
          type="search"
          value={ask.q}
          onChange={(e) => {
            ask.setQ(e.target.value);
            onTyping();
          }}
          placeholder="What did I decide about…"
          aria-label="Question"
        />
        {ask.effort === "quick" ? null : (
          <button type="submit" className="nt-btn nt-btn--primary" disabled={!ask.q.trim() || ask.asked?.state === "asking"}>
            {ask.asked?.state === "asking" ? "Asking…" : "Ask"}
          </button>
        )}
      </div>
    </form>
  );
}

function AskPanel({ ask }: { ask: AskState }) {
  const { at, setAt, effort, setEffort, since, setSince, until, setUntil, kinds, setKinds, topics, setTopics, entities, setEntities, open, setOpen, asked, saved, quick, q } = ask;
  return (
    <div className="nt-ask">
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
      {asked ? <AnswerCard asked={asked} onSave={() => void ask.saveAnswer(asked)} saved={saved} onFollowUp={(f) => ask.setQ(f)} /> : null}
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
      {!asked && !quick.data && !quick.loading ? <p className="nt-muted nt-block">Type a question above. Quick shows matching notes as you type; Standard and Deep answer with their sources.</p> : null}
    </div>
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
  // The most noted first: the one most likely to have changed.
  const candidates = [...uniqueEntities(overview.data?.entities)].sort((x, y) => ((y as { note_count?: number }).note_count || 0) - ((x as { note_count?: number }).note_count || 0));
  const first = a || candidates[0]?.entity_id || "";
  return (
    <div className="nt-thennow-panel">
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
    </div>
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

// ---------------------------------------------------------------- review cards

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
    <li className="nt-item nt-thought" data-kind={item.kind}>
      <p className="nt-review__pair">
        <span>{head}</span>
        {a ? <SideChip side={a} /> : null}
        <span className="nt-muted">{item.kind === "merge" ? "into" : item.kind === "close_match" ? "and" : "versus"}</span>
        {b ? <SideChip side={b} /> : null}
        <span className="nt-link__date">{shortDate(item.at)}</span>
      </p>
      {own.data ? (
        <p className="nt-thought__body">{`${own.data.name} on its own: ${own.data.note_count === 1 ? "1 note" : `${own.data.note_count} notes`}${own.data.summary ? `; ${own.data.summary}` : ""}`}</p>
      ) : null}
      <WhyLines id={item.review_id} line={`${reviewWhy(item.why)}${item.reason ? ` ${item.reason}` : ""}`} evidence={item.evidence_note_ids} />
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
