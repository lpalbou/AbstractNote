/**
 * Processing (operator, 2026-10-20): what the notebook is doing with your notes, live. One screen, no page
 * scroll: the state as one sentence with its actions; Right now (the work in progress with its steps, and the
 * consolidation); Pace (the last two hours, the queue by priority, the waiting notes); Just understood (the
 * newest revisions as they land); Needs attention (failed notes, helpers that need a repair, the last look
 * back). GET /note/processing every 3 s while the page is visible (lib/processing.ts createPoller).
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { AfSwitch, Icon } from "@abstractframework/ui-kit";
import { ApiError, errorSentence, type Api } from "../lib/api";
import { useNarrow, type ReadState } from "../lib/hooks";
import {
  CONSOLIDATE_CHOICES,
  CONSOLIDATION_STAGES,
  PRIORITY_WORDS,
  agoWords,
  automationCommand,
  cancelWork,
  clockWords,
  consolidationNotice,
  consolidationStageIndex,
  createPoller,
  drainNotes,
  durationWords,
  etaWords,
  everyWords,
  flowWords,
  groupActive,
  lastConsolidationLine,
  moveToFront,
  orderWaiting,
  paceGeometry,
  paceWords,
  pendingConsolidationLine,
  queueSegments,
  revisionChips,
  stateLine,
  stepIndex,
  stepperFor,
  type WaitingNote,
} from "../lib/processing";
import type { ActiveWork, Consolidation, EnsureResult, NoteSettings, Processing, QueueInfo } from "../lib/types";
import { KindBadge, Spinner, openNote, useApp } from "../components/common";
import { capitalize } from "../lib/words";

const hidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";

/** The poller as a hook: the answer, the server clock offset, and whether the last reads failed. */
function useProcessing(api: Api) {
  const [data, setData] = useState<Processing | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [failures, setFailures] = useState(0);
  const [offset, setOffset] = useState(0);
  const poller = useMemo(
    () =>
      createPoller<Processing>({
        read: (etag) => api.poll<Processing>("note/processing", etag),
        hidden,
        onData: (d) => {
          setData(d);
          const t = Date.parse(d.now);
          if (Number.isFinite(t)) setOffset(t - Date.now());
        },
        onFresh: () => {
          setError(null);
          setFailures(0);
        },
        onError: (e, n) => {
          setError(e);
          setFailures(n);
        },
      }),
    [api],
  );
  useEffect(() => {
    poller.start();
    const on = () => poller.visibilityChanged();
    document.addEventListener("visibilitychange", on);
    return () => {
      document.removeEventListener("visibilitychange", on);
      poller.stop();
    };
  }, [poller]);
  const refresh = useCallback(() => void poller.refresh(), [poller]);
  return { data, error, failures, offset, refresh };
}

/** The server's "now", ticking every second while something runs (elapsed clocks). */
function useServerNow(offset: number, ticking: boolean): number {
  const [now, setNow] = useState(() => Date.now() + offset);
  useEffect(() => {
    setNow(Date.now() + offset);
    if (!ticking) return;
    const t = setInterval(() => {
      if (!hidden()) setNow(Date.now() + offset);
    }, 1000);
    return () => clearInterval(t);
  }, [offset, ticking]);
  return now;
}

type Section = "now" | "pace" | "recent" | "attention";
const SECTIONS: Array<[Section, string]> = [
  ["now", "Right now"],
  ["pace", "Pace"],
  ["recent", "Just understood"],
  ["attention", "Needs attention"],
];

type Props = { settings: ReadState<NoteSettings | null>; onSave: (patch: Partial<NoteSettings>) => Promise<void> };

export function ProcessingView({ settings, onSave }: Props) {
  const { api } = useApp();
  const narrow = useNarrow(900);
  const live = useProcessing(api);
  const p = live.data;
  const ticking = Boolean(p && (p.active.length || p.consolidation?.run));
  const now = useServerNow(live.offset, ticking);
  const [section, setSection] = useState<Section>("now");
  const [line, setLine] = useState("");
  const [busy, setBusy] = useState("");

  const act = useCallback(
    async (key: string, fn: () => Promise<unknown>, done: string, failed: string) => {
      setBusy(key);
      setLine("");
      try {
        await fn();
        setLine(done);
        live.refresh();
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) setLine("Already running.");
        else if (e instanceof ApiError && e.status === 401) setLine("Please sign in again.");
        else setLine(failed);
      } finally {
        setBusy("");
      }
    },
    [live],
  );

  if (!p) {
    return (
      <div className="nt-center">
        {live.error ? (
          <div className="nt-error" role="alert">
            <span>{errorSentence(live.error, "what your notebook is doing")}</span>
            <button type="button" className="nt-btn nt-btn--quiet" onClick={live.refresh}>
              Retry
            </button>
          </div>
        ) : (
          <Spinner label="Looking at what your notebook is doing…" />
        )}
      </div>
    );
  }

  const unhealthy = !p.processor.healthy || !p.reflect.healthy || p.consolidation?.automation.healthy === false;
  const processNow = () =>
    act(
      "process",
      async () => {
        let id = p.processor.id;
        if (!id || !p.processor.healthy) id = (await api.post<EnsureResult>("note/processor/ensure")).processor_id;
        if (!id) throw new ApiError(500, "no processor", "no_processor");
        await automationCommand(api, id, "automation.run_now");
      },
      "Processing started.",
      "Processing could not be started.",
    );
  const repair = () => act("repair", () => api.post("note/processor/ensure"), "Repaired.", "The repair did not work; try again in a minute.");
  const reflectNow = () =>
    act(
      "reflect",
      async () => {
        if (!p.reflect.id) throw new ApiError(404, "no reflect", "missing");
        await automationCommand(api, p.reflect.id, "automation.run_now");
      },
      "Looking back over your notes now.",
      "The look back could not be started.",
    );
  const togglePaused = (on: boolean) =>
    act(
      "pause",
      async () => {
        if (!p.processor.id) throw new ApiError(404, "no processor", "missing");
        await automationCommand(api, p.processor.id, on ? "automation.resume" : "automation.pause");
      },
      on ? "Automatic understanding is on." : "Automatic understanding is off; the note in progress finishes first.",
      "That change was not saved.",
    );

  const state = stateLine(p);
  const stale = live.failures >= 2;
  const counts: Record<Section, number> = { now: p.active.filter((a) => a.flow !== "note-process").length + (p.consolidation?.run ? 1 : 0), pace: p.queue.queued, recent: p.recent.length, attention: p.failed.length + (unhealthy ? 1 : 0) };

  const columns = {
    now: (
      <RightNow p={p} now={now} api={api} onChanged={live.refresh} settings={settings} onSave={onSave} busy={busy} act={act} />
    ),
    pace: <Pace p={p} api={api} onChanged={live.refresh} />,
    recent: <JustUnderstood p={p} now={now} />,
    attention: <NeedsAttention p={p} now={now} api={api} busy={busy} onRepair={repair} onReflect={reflectNow} onChanged={live.refresh} />,
  };

  return (
    <div className={`nt-proc${narrow ? " nt-proc--narrow" : ""}`}>
      <header className="nt-proc__strip">
        <div className="nt-proc__state" aria-live="polite">
          <span className={`nt-proc__dot nt-proc__dot--${state.tone}`} aria-hidden="true" />
          <h1 className="nt-proc__sentence">{state.text}</h1>
          {stale ? <span className="nt-proc__stale">Not updated for {live.failures * 3} s</span> : null}
        </div>
        <div className="nt-proc__actions">
          <button type="button" className="nt-capsule nt-capsule--primary" disabled={busy === "process"} onClick={() => void processNow()}>
            <Icon name="play" size={14} />
            <span>{busy === "process" ? "Starting…" : "Process now"}</span>
          </button>
          <span className="nt-capsule nt-capsule--switch">
            <AfSwitch label="Automatic understanding" checked={!p.processor.paused} busy={busy === "pause"} unavailableReason={p.processor.id ? null : "Your notebook has not set this up yet; Process now does."} reasonVisible={false} onChange={(next) => void togglePaused(next)} />
          </span>
          {unhealthy ? (
            <button type="button" className="nt-capsule nt-capsule--warn" disabled={busy === "repair"} onClick={() => void repair()}>
              <Icon name="warning" size={14} />
              <span>{busy === "repair" ? "Repairing…" : "Repair"}</span>
            </button>
          ) : null}
          <button type="button" className="nt-capsule" disabled={busy === "reflect" || !p.reflect.id} onClick={() => void reflectNow()}>
            <Icon name="sparkle" size={14} />
            <span>{busy === "reflect" ? "Starting…" : "Reflect now"}</span>
          </button>
        </div>
        {line ? (
          <p className="nt-proc__line" role="status">
            {line}
          </p>
        ) : null}
      </header>
      {narrow ? (
        <>
          <div className="nt-proc__pills" role="tablist" aria-label="Processing">
            {SECTIONS.map(([id, label]) => (
              <button key={id} id={`proc-tab-${id}`} type="button" role="tab" className="nt-proc__pill" aria-selected={section === id} aria-controls={`proc-sec-${id}`} onClick={() => setSection(id)}>
                <span>{label}</span>
                {counts[id] ? <span className="nt-proc__count">{counts[id]}</span> : null}
              </button>
            ))}
          </div>
          <div className="nt-proc__one" id={`proc-sec-${section}`} role="tabpanel" aria-labelledby={`proc-tab-${section}`} data-section={section}>
            {columns[section]}
          </div>
        </>
      ) : (
        <div className="nt-proc__grid">
          <div className="nt-proc__col" data-section="now">
            {columns.now}
          </div>
          <div className="nt-proc__col" data-section="pace">
            {columns.pace}
          </div>
          <div className="nt-proc__col nt-proc__col--split">
            <div data-section="recent">{columns.recent}</div>
            <div data-section="attention">{columns.attention}</div>
          </div>
        </div>
      )}
    </div>
  );
}

function Panel({ title, sub, actions, children, className, id }: { title: string; sub?: string; actions?: React.ReactNode; children: React.ReactNode; className?: string; id?: string }) {
  const hid = useId();
  return (
    <section className={`nt-proc-panel${className ? ` ${className}` : ""}`} aria-labelledby={hid} id={id}>
      <header className="nt-proc-panel__head">
        <h2 id={hid}>
          {title}
          {sub ? <span className="nt-proc-panel__sub">{sub}</span> : null}
        </h2>
        {actions ? <div className="nt-proc-panel__actions">{actions}</div> : null}
      </header>
      <div className="nt-proc-panel__body">{children}</div>
    </section>
  );
}

// ------------------------------------------------------------------ Right now

type Act = (key: string, fn: () => Promise<unknown>, done: string, failed: string) => Promise<void>;

function RightNow({ p, now, api, onChanged, settings, onSave, busy, act }: { p: Processing; now: number; api: Api; onChanged: () => void; settings: ReadState<NoteSettings | null>; onSave: (patch: Partial<NoteSettings>) => Promise<void>; busy: string; act: Act }) {
  const groups = groupActive(p.active);
  return (
    <div className="nt-proc__stack">
      <Panel title="Right now" className="nt-proc-panel--grow">
        {groups.length ? (
          <ul className="nt-proc-cards">
            {groups.map((g) => (
              <li key={g.item.run_id}>
                {g.item.flow === "note-drain" ? <DrainCard group={g} now={now} api={api} onChanged={onChanged} /> : <WorkCard item={g.item} now={now} />}
              </li>
            ))}
          </ul>
        ) : (
          <p className="nt-proc-empty">
            {p.queue.queued ? "Nothing is being understood this moment; the waiting notes are taken on the next pass." : "Nothing is running. Everything you sent is understood."}
          </p>
        )}
      </Panel>
      <ConsolidationCard c={p.consolidation} now={now} api={api} settings={settings} onSave={onSave} busy={busy} act={act} />
    </div>
  );
}

function Stepper({ steps, current, label }: { steps: string[]; current: number; label: string }) {
  return (
    <ol className="nt-stepper" aria-label={label}>
      {steps.map((s, i) => {
        const state = current < 0 ? "todo" : i < current ? "done" : i === current ? "current" : "todo";
        return (
          <li key={s} className={`nt-stepper__step nt-stepper__step--${state}`} aria-current={state === "current" ? "step" : undefined}>
            <span className="nt-stepper__mark" aria-hidden="true">
              {state === "done" ? <Icon name="check" size={10} /> : null}
            </span>
            <span className="nt-stepper__label">{s}</span>
            {state === "done" ? <span className="nt-sr"> (done)</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

function Meter({ done, total, label }: { done: number; total: number; label: string }) {
  const share = total > 0 ? Math.min(1, Math.max(0, done / total)) : 0;
  return (
    <div className="nt-meter">
      <div className="nt-meter__track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
        <span className="nt-meter__fill" style={{ width: `${(share * 100).toFixed(1)}%` }} />
      </div>
      <span className="nt-meter__words">{label}</span>
    </div>
  );
}

function elapsedOf(iso: string | null | undefined, now: number): number {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? Math.max(0, now - t) : 0;
}

function ThinkingPill({ thinking }: { thinking: ActiveWork["thinking"] }) {
  if (!thinking) return null;
  return (
    <span className={`nt-pill${thinking === "on" ? " nt-pill--on" : ""}`} title={thinking === "on" ? "The model thinks before it answers (slower, more careful)." : "The model answers without thinking first (faster)."}>
      {thinking === "on" ? "Thinking" : "Quick"}
    </span>
  );
}

function WorkCard({ item, now, nested }: { item: ActiveWork; now: number; nested?: boolean }) {
  const stepper = stepperFor(item);
  const idx = stepIndex(item);
  const title = item.note?.title || flowWords(item.flow);
  return (
    <article className={`nt-proc-card${nested ? " nt-proc-card--nested" : ""}`} data-flow={item.flow}>
      <div className="nt-proc-card__head">
        {item.note ? (
          <button type="button" className="nt-proc-card__title nt-proc-card__title--link" onClick={() => openNote(item.note!.note_id)}>
            {title}
          </button>
        ) : (
          <span className="nt-proc-card__title">{title}</span>
        )}
        {item.note?.kind ? <KindBadge kind={item.note.kind} /> : null}
        <ThinkingPill thinking={item.thinking} />
        <span className="nt-proc-card__clock" title="Time spent so far">
          {clockWords(elapsedOf(item.started_at, now))}
        </span>
      </div>
      {stepper ? <Stepper steps={stepper.map((s) => s.label)} current={idx} label={`Steps of ${title}`} /> : null}
      {item.sections_total ? <Meter done={item.sections_done || 0} total={item.sections_total} label={`${item.sections_done || 0} of ${item.sections_total} ${item.sections_total === 1 ? "section" : "sections"}`} /> : null}
    </article>
  );
}

function DrainCard({ group, now, api, onChanged }: { group: { item: ActiveWork; children: ActiveWork[] }; now: number; api: Api; onChanged: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [line, setLine] = useState("");
  const notes = drainNotes(group);
  const stop = async () => {
    setBusy(true);
    setLine("");
    try {
      await cancelWork(api, group.item.run_id);
      setConfirm(false);
      setLine("Stopped; the note in progress goes back to the queue.");
      onChanged();
    } catch {
      setLine("It could not be stopped; try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="nt-proc-card nt-proc-card--drain" data-flow="note-drain">
      <div className="nt-proc-card__head">
        <Icon name="loop" size={14} className="nt-proc-card__icon" />
        <span className="nt-proc-card__title">{flowWords("note-drain")}</span>
        <span className="nt-proc-card__clock" title="Time spent so far">
          {clockWords(elapsedOf(group.item.started_at, now))}
        </span>
        {!confirm ? (
          <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={() => setConfirm(true)}>
            Cancel
          </button>
        ) : null}
      </div>
      {confirm ? (
        <div className="nt-confirm nt-proc-confirm" role="group" aria-label="Stop working through the queue">
          <span>Stop working through the queue? The note in progress goes back to the queue.</span>
          <button type="button" className="nt-btn nt-btn--danger nt-btn--sm" disabled={busy} onClick={() => void stop()} autoFocus>
            {busy ? "Stopping…" : "Stop"}
          </button>
          <button type="button" className="nt-btn nt-btn--sm" onClick={() => setConfirm(false)}>
            Keep going
          </button>
        </div>
      ) : null}
      {notes.length ? (
        <ul className="nt-proc-cards nt-proc-cards--nested">
          {notes.map((n) => (
            <li key={n.run_id}>
              <WorkCard item={n} now={now} nested />
            </li>
          ))}
        </ul>
      ) : (
        <p className="nt-muted">Taking the next note…</p>
      )}
      {line ? (
        <p className="nt-muted" role="status">
          {line}
        </p>
      ) : null}
    </article>
  );
}

function ConsolidationCard({ c, now, api, settings, onSave, busy, act }: { c: Consolidation | null; now: number; api: Api; settings: ReadState<NoteSettings | null>; onSave: (patch: Partial<NoteSettings>) => Promise<void>; busy: string; act: Act }) {
  if (!c) {
    return (
      <Panel title="Consolidation" className="nt-proc-panel--cons">
        <p className="nt-proc-empty">Consolidation is not available in your notebook yet.</p>
      </Panel>
    );
  }
  const current = c["run"];
  const idx = current ? consolidationStageIndex(current.stage) : -1;
  const stage = idx >= 0 && idx < CONSOLIDATION_STAGES.length ? CONSOLIDATION_STAGES[idx].label : "";
  const pending = pendingConsolidationLine(c.pending);
  const elapsed = current ? Math.max(current.elapsed_s * 1000, elapsedOf(current.started_at, now)) : 0;
  const consolidateNow = () =>
    act(
      "consolidate",
      async () => {
        if (!c.automation.id) throw new ApiError(404, "missing", "missing");
        await automationCommand(api, c.automation.id, "automation.run_now");
      },
      "Consolidating now.",
      "Consolidation could not be started.",
    );
  return (
    <Panel
      title="Consolidation"
      sub={current ? "in progress" : c.automation.paused ? "paused" : everyWords(c.automation.schedule?.every) || undefined}
      className="nt-proc-panel--cons"
      actions={
        <button type="button" className="nt-capsule nt-capsule--sm" disabled={busy === "consolidate" || Boolean(current) || !c.automation.id} onClick={() => void consolidateNow()}>
          <Icon name="refresh" size={13} />
          <span>{busy === "consolidate" ? "Starting…" : "Consolidate now"}</span>
        </button>
      }
    >
      <Stepper steps={CONSOLIDATION_STAGES.map((s) => s.label)} current={current ? idx : -1} label="Stages of consolidation" />
      {current ? (
        <div className="nt-proc-cons__run">
          <Meter done={current.stage_done} total={current.stage_total} label={`${stage} · ${current.stage_done} of ${current.stage_total}`} />
          <p className="nt-proc-cons__time">
            <span>{clockWords(elapsed)} so far</span>
            {etaWords(current.eta_s) ? <span>{etaWords(current.eta_s)}</span> : null}
          </p>
        </div>
      ) : null}
      <p className="nt-proc-cons__last">{lastConsolidationLine(c.last, now)}</p>
      {consolidationNotice(c.last) ? <p className="nt-muted nt-proc-cons__pending nt-proc-cons__notice">{consolidationNotice(c.last)}</p> : null}
      {pending ? <p className="nt-muted nt-proc-cons__pending">Waiting: {pending}</p> : null}
      <ConsolidateSchedule settings={settings} onSave={onSave} fallback={c.automation.schedule?.every} />
    </Panel>
  );
}

/** The consolidation schedule (settings `consolidate_schedule`); Settings shows the same picker. */
export function ConsolidateSchedule({ settings, onSave, fallback }: { settings: ReadState<NoteSettings | null>; onSave: (patch: Partial<NoteSettings>) => Promise<void>; fallback?: string | null }) {
  const [saving, setSaving] = useState(false);
  const [line, setLine] = useState("");
  const value = settings.data?.consolidate_schedule?.every || fallback || "1d";
  if (settings.data && !settings.data.consolidate_schedule) return <p className="nt-muted">Consolidation is not available in your notebook yet.</p>;
  return (
    <label className="nt-field nt-field--inline nt-proc-schedule">
      <span className="nt-field__label">Consolidate</span>
      <select
        className="nt-input"
        value={value}
        disabled={saving || !settings.data}
        onChange={async (e) => {
          setSaving(true);
          setLine("");
          try {
            await onSave({ consolidate_schedule: { every: e.target.value } });
            setLine("Saved.");
          } catch {
            setLine("That change was not saved.");
          } finally {
            setSaving(false);
          }
        }}
      >
        {CONSOLIDATE_CHOICES.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
      {line ? (
        <span className="nt-muted" role="status">
          {line}
        </span>
      ) : null}
    </label>
  );
}

// ------------------------------------------------------------------ Pace

function Pace({ p, api, onChanged }: { p: Processing; api: Api; onChanged: () => void }) {
  const segs = queueSegments(p.queue.by_priority);
  const total = segs.reduce((a, s) => a + s.count, 0);
  const now5 = paceWords(p.pace.per_minute_5m);
  const hour = paceWords(p.pace.per_minute_1h);
  return (
    <Panel title="Pace" sub="the last two hours" className="nt-proc-panel--grow">
      <div className="nt-proc-figures">
        <div className="nt-proc-figure">
          <span className="nt-proc-figure__value">{now5 || "None"}</span>
          <span className="nt-proc-figure__label">in the last 5 minutes</span>
        </div>
        <div className="nt-proc-figure">
          <span className="nt-proc-figure__value">{hour || "None"}</span>
          <span className="nt-proc-figure__label">over the last hour</span>
        </div>
      </div>
      <PaceLine series={p.pace.series} />
      <div className="nt-proc-queue">
        <div className="nt-proc-queue__head">
          <span className="nt-proc-queue__total">{total === 1 ? "1 note waiting" : `${total} notes waiting`}</span>
          {etaWords(p.queue.eta_s) ? <span className="nt-muted">{etaWords(p.queue.eta_s)}</span> : null}
        </div>
        {total ? (
          <>
            <div className="nt-stack" role="img" aria-label={`Waiting by priority: ${segs.filter((s) => s.count).map((s) => `${s.label} ${s.count}`).join(", ")}`}>
              {segs
                .filter((s) => s.count)
                .map((s) => (
                  <span key={s.key} className={`nt-stack__seg nt-stack__seg--p${s.key}`} style={{ flexGrow: s.count }} title={`${s.label}: ${s.count}`} />
                ))}
            </div>
            <ul className="nt-qlegend" aria-hidden="true">
              {segs.map((s) => (
                <li key={s.key} className={s.count ? undefined : "nt-qlegend__item--zero"}>
                  <span className={`nt-qlegend__swatch nt-stack__seg--p${s.key}`} />
                  <span>{s.label}</span>
                  <span className="nt-qlegend__count">{s.count}</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </div>
      <Waiting api={api} queueKey={`${p.queue.queued}|${JSON.stringify(p.queue.by_priority)}|${p.queue.processing}`} onChanged={onChanged} />
    </Panel>
  );
}

function PaceLine({ series }: { series: Processing["pace"]["series"] }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(360);
  const [hover, setHover] = useState<number | null>(null);
  const height = 112;
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => setWidth(Math.max(200, Math.round(entries[0].contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const g = paceGeometry(series, width, height);
  const last = g.points[g.points.length - 1];
  const prev = g.points[g.points.length - 2];
  const seg = (pts: typeof g.points) => pts.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const solid = g.points.length > 1 ? seg(g.points.slice(0, -1)) : g.path;
  const partial = prev && last ? seg([prev, last]) : "";
  const at = hover !== null ? g.points[hover] : null;
  const hm = (t: number) => {
    const d = new Date(t);
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  };
  const total = series.reduce((a, s) => a + (Number(s.understood) || 0), 0);
  const pick = (clientX: number) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box || !g.points.length) return;
    const x = clientX - box.left;
    let best = 0;
    for (let i = 1; i < g.points.length; i++) if (Math.abs(g.points[i].x - x) < Math.abs(g.points[best].x - x)) best = i;
    setHover(best);
  };
  return (
    <div className="nt-paceline" ref={ref}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        tabIndex={0}
        aria-label={`Notes understood every 5 minutes over the last two hours: ${total} in all, at most ${Math.max(0, ...g.points.map((p) => p.understood))} in 5 minutes. Use the arrow keys to read each 5 minutes.`}
        onPointerMove={(e) => pick(e.clientX)}
        onPointerLeave={() => setHover(null)}
        onBlur={() => setHover(null)}
        onKeyDown={(e) => {
          if (!g.points.length) return;
          if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? g.points.length) - 1));
          else if (e.key === "ArrowRight") setHover((h) => Math.min(g.points.length - 1, (h ?? -1) + 1));
          else return;
          e.preventDefault();
        }}
      >
        <line className="nt-paceline__grid" x1={g.left} x2={g.right} y1={g.top} y2={g.top} />
        <line className="nt-paceline__axis" x1={g.left} x2={g.right} y1={g.baseY} y2={g.baseY} />
        <text className="nt-paceline__tick" x={g.left - 6} y={g.top + 4} textAnchor="end">
          {g.max}
        </text>
        <text className="nt-paceline__tick" x={g.left - 6} y={g.baseY + 4} textAnchor="end">
          0
        </text>
        <text className="nt-paceline__tick" x={g.left} y={height - 3} textAnchor="start">
          2 h ago
        </text>
        <text className="nt-paceline__tick" x={(g.left + g.right) / 2} y={height - 3} textAnchor="middle">
          1 h ago
        </text>
        <text className="nt-paceline__tick" x={g.right} y={height - 3} textAnchor="end">
          now
        </text>
        {g.area ? <path className="nt-paceline__area" d={g.area} /> : null}
        {/* The last 5 minutes are still filling: that segment is dashed, so its dip does not read as a drop. */}
        {solid ? <path className="nt-paceline__line" d={solid} /> : null}
        {partial ? <path className="nt-paceline__line nt-paceline__line--partial" d={partial} /> : null}
        {at ? <line className="nt-paceline__cross" x1={at.x} x2={at.x} y1={g.top} y2={g.baseY} /> : null}
        {(at || last) ? <circle className="nt-paceline__dot" cx={(at || last)!.x} cy={(at || last)!.y} r={4} /> : null}
      </svg>
      {at ? (
        <div className="nt-paceline__tip" role="status" style={at.x > width / 2 ? { right: width - at.x + 8 } : { left: at.x + 8 }}>
          <strong>{at.understood === 1 ? "1 note" : `${at.understood} notes`}</strong>
          <span>{at === last ? `since ${hm(at.t)}, so far` : `${hm(at.t)} to ${hm(at.t + 300_000)}`}</span>
        </div>
      ) : null}
    </div>
  );
}

function Waiting({ api, queueKey, onChanged }: { api: Api; queueKey: string; onChanged: () => void }) {
  const [waiting, setWaiting] = useState<WaitingNote[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState("");
  const [line, setLine] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    api.get<QueueInfo>("note/queue").then(
      (q) => {
        if (!live) return;
        setWaiting(q.waiting || []);
        setFailed(false);
      },
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [api, queueKey, tick]);
  const rows = useMemo(() => orderWaiting(waiting || [], search), [waiting, search]);
  const front = async (n: WaitingNote) => {
    setBusy(n.note_id);
    setLine("");
    try {
      await moveToFront(api, n.note_id);
      setLine(`"${n.title || "The note"}" is next in line.`);
      setTick((t) => t + 1);
      onChanged();
    } catch (e) {
      setLine(e instanceof ApiError && e.status === 404 ? "That note is no longer waiting." : "It could not be moved.");
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="nt-waiting">
      <div className="nt-waiting__head">
        <h3>Waiting notes</h3>
        <input className="nt-input nt-waiting__search" type="search" placeholder="Find a waiting note" aria-label="Find a waiting note" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>
      {line ? (
        <p className="nt-muted" role="status">
          {line}
        </p>
      ) : null}
      {waiting === null ? (
        failed ? (
          <div className="nt-error" role="alert">
            <span>The waiting notes could not be loaded.</span>
            <button type="button" className="nt-btn nt-btn--quiet" onClick={() => setTick((t) => t + 1)}>
              Retry
            </button>
          </div>
        ) : (
          <Spinner />
        )
      ) : rows.length ? (
        <ol className="nt-waiting__list">
          {rows.map((n, i) => (
            <li key={n.note_id} className="nt-waiting__row">
              <span className="nt-waiting__pos">{i + 1}</span>
              <button type="button" className="nt-waiting__title" onClick={() => openNote(n.note_id)} title={n.title}>
                {n.title || "Untitled note"}
              </button>
              <span className={`nt-pill nt-pill--p${n.priority}`}>{PRIORITY_WORDS[String(n.priority)] || "Normal"}</span>
              {n.priority >= 3 ? (
                <span className="nt-waiting__first">First in line</span>
              ) : (
                <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" disabled={busy === n.note_id} onClick={() => void front(n)}>
                  {busy === n.note_id ? "Moving…" : "Move to front"}
                </button>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <p className="nt-proc-empty">{search ? "No waiting note has that in its title." : "No note is waiting."}</p>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Just understood

function JustUnderstood({ p, now }: { p: Processing; now: number }) {
  const seen = useRef<Set<string> | null>(null);
  const fresh = new Set<string>();
  if (seen.current === null) seen.current = new Set(p.recent.map((r) => r.revision_id));
  else for (const r of p.recent) if (!seen.current.has(r.revision_id)) fresh.add(r.revision_id);
  useEffect(() => {
    for (const r of p.recent) seen.current!.add(r.revision_id);
  }, [p.recent]);
  return (
    <Panel title="Just understood" sub={p.recent.length ? `the last ${p.recent.length}` : undefined} className="nt-proc-panel--grow">
      {p.recent.length ? (
        <ol className="nt-feed" aria-live="polite">
          {p.recent.map((r) => {
            const chips = revisionChips(r);
            return (
              <li key={r.revision_id} className={`nt-feed__item${fresh.has(r.revision_id) ? " nt-feed__item--new" : ""}`}>
                <button type="button" className="nt-feed__open" onClick={() => openNote(r.note.note_id)}>
                  <span className="nt-feed__top">
                    <span className="nt-feed__title">{r.note.title || "Untitled note"}</span>
                    <span className="nt-feed__when">{agoWords(r.recorded_at, now)}</span>
                  </span>
                  {r.summary ? <span className="nt-feed__summary">{r.summary}</span> : null}
                  <span className="nt-feed__chips">
                    {chips.length ? chips.map((c) => <span key={c} className={`nt-chip${c.endsWith("replaced") ? " nt-chip--replaced" : ""}`}>{c}</span>) : <span className="nt-chip nt-chip--quiet">Nothing new</span>}
                    {r.duration_s !== null && r.duration_s !== undefined ? <span className="nt-feed__dur">{durationWords(r.duration_s)}</span> : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="nt-proc-empty">Notes appear here as soon as they are understood.</p>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------ Needs attention

const HELPERS: Array<["processor" | "reflect" | "consolidation", string]> = [
  ["processor", "Understanding new notes"],
  ["reflect", "Looking back"],
  ["consolidation", "Consolidation"],
];

function NeedsAttention({ p, now, api, busy, onRepair, onReflect, onChanged }: { p: Processing; now: number; api: Api; busy: string; onRepair: () => void; onReflect: () => void; onChanged: () => void }) {
  const [retrying, setRetrying] = useState("");
  const [line, setLine] = useState("");
  const retry = async (noteId: string) => {
    setRetrying(noteId);
    setLine("");
    try {
      await api.post(`note/queue/${encodeURIComponent(noteId)}/retry`);
      setLine("The note is back in the queue.");
      onChanged();
    } catch {
      setLine("That note could not be queued again.");
    } finally {
      setRetrying("");
    }
  };
  const helpers = HELPERS.map(([key, label]) => {
    const a = key === "consolidation" ? p.consolidation?.automation : p[key];
    return a ? { key, label, a } : null;
  }).filter(Boolean) as Array<{ key: string; label: string; a: Processing["processor"] }>;
  const broken = helpers.filter((h) => !h.a.healthy);
  const r = p.reflect;
  const themes = r.communities;
  const reflectLine = [
    r.last_run_at ? `Last looked back ${agoWords(r.last_run_at, now)}` : "Has not looked back yet",
    r.insights_last_run !== null && r.insights_last_run !== undefined ? (r.insights_last_run === 1 ? "1 insight written" : `${r.insights_last_run} insights written`) : "",
    themes ? [themes.count === 1 ? "1 theme" : `${themes.count} themes`, themes.unlabelled ? `${themes.unlabelled} not named yet` : "", themes.stale_label ? `${themes.stale_label} changed since named` : ""].filter(Boolean).join(", ") : "",
  ].filter(Boolean);
  return (
    <Panel title="Needs attention" sub={p.failed.length || broken.length ? undefined : "all clear"} className="nt-proc-panel--grow">
      {line ? (
        <p className="nt-muted" role="status">
          {line}
        </p>
      ) : null}
      <ul className="nt-attn">
        {broken.map((h) => (
          <li key={h.key} className="nt-attn__row nt-attn__row--broken">
            <Icon name="warning" size={14} className="nt-attn__icon" />
            <div className="nt-attn__text">
              <span className="nt-attn__title">{h.label} needs a repair</span>
              {h.a.reason ? <span className="nt-attn__why">{h.a.reason}</span> : null}
            </div>
            <button type="button" className="nt-btn nt-btn--sm" disabled={busy === "repair"} onClick={onRepair}>
              {busy === "repair" ? "Repairing…" : "Repair"}
            </button>
          </li>
        ))}
        {p.failed.map((f) => (
          <li key={f.note_id} className="nt-attn__row">
            <Icon name="error" size={14} className="nt-attn__icon nt-attn__icon--error" />
            <div className="nt-attn__text">
              <button type="button" className="nt-attn__title nt-attn__title--link" onClick={() => openNote(f.note_id)}>
                {f.title || "Untitled note"}
              </button>
              <span className="nt-attn__why">{f.error || "It could not be understood."}</span>
              <span className="nt-attn__meta">
                {[f.attempts ? (f.attempts === 1 ? "Tried once" : `Tried ${f.attempts} times`) : "", f.failed_at ? agoWords(f.failed_at, now) : "", f.next_attempt_at ? `next try ${agoWords(f.next_attempt_at, now).replace(" ago", "")}` : ""].filter(Boolean).join(" · ")}
              </span>
            </div>
            <button type="button" className="nt-btn nt-btn--sm" disabled={retrying === f.note_id} onClick={() => void retry(f.note_id)}>
              {retrying === f.note_id ? "Retrying…" : "Retry"}
            </button>
          </li>
        ))}
      </ul>
      {!p.failed.length && !broken.length ? <p className="nt-proc-empty">Nothing needs you: every note was understood.</p> : null}
      <div className="nt-helpers">
        <h3>Helpers</h3>
        <ul className="nt-helpers__list">
          {helpers.map((h) => (
            <li key={h.key} className="nt-helpers__row">
              <span className={`nt-proc__dot nt-proc__dot--${!h.a.healthy ? "broken" : h.a.paused ? "paused" : "idle"}`} aria-hidden="true" />
              <span className="nt-helpers__name">{h.label}</span>
              <span className="nt-helpers__state">{!h.a.healthy ? "Needs a repair" : h.a.paused ? "Paused" : capitalize(everyWords(h.a.schedule?.every) || "on")}</span>
            </li>
          ))}
        </ul>
        <div className="nt-helpers__reflect">
          <p className="nt-muted">{reflectLine.join(" · ")}.</p>
          <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" disabled={busy === "reflect" || !r.id} onClick={onReflect}>
            {busy === "reflect" ? "Starting…" : "Reflect now"}
          </button>
        </div>
      </div>
    </Panel>
  );
}
