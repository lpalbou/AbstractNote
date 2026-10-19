import { useEffect, useState } from "react";
import { AfDrawer, AfSwitch, ProviderModelSelect, randomId, type ProviderOption } from "@abstractframework/ui-kit";
import { ApiError, errorSentence } from "../lib/api";
import { useRead, type ReadState } from "../lib/hooks";
import { locationEnabled, setLocationEnabled } from "../lib/location";
import type { ModelChoice, NoteSettings, NoteStatus, QueueInfo } from "../lib/types";
import { NoteLink, ReadView, useApp } from "../components/common";

export const REFLECT_CHOICES: Array<[string, string]> = [
  ["1d", "Every day"],
  ["7d", "Every week"],
  ["30d", "Every month"],
];

/** The processor automation's documented defaults (docs/cognition.md "note-drain and the queue"): the queue answer does not carry them. */
export const PROCESSOR_DEFAULTS = { every: "2m", max_items: 20 };

/** "2m" -> "2 minutes" (the processor's interval as words). */
export function everyWords(every: string | undefined): string {
  const m = String(every || "").match(/^(\d+)([smhd])$/);
  if (!m) return "regularly";
  const n = Number(m[1]);
  const unit = { s: "second", m: "minute", h: "hour", d: "day" }[m[2] as "s" | "m" | "h" | "d"];
  return n === 1 ? `every ${unit}` : `every ${n} ${unit}s`;
}

export function queueSentence(q: QueueInfo): string {
  const s = q.stats;
  const parts: string[] = [];
  parts.push(s.queued === 0 ? "Nothing is waiting." : s.queued === 1 ? "1 note is waiting." : `${s.queued} notes are waiting.`);
  if (s.processing) parts.push(s.processing === 1 ? "1 is being processed." : `${s.processing} are being processed.`);
  parts.push(`Up to ${PROCESSOR_DEFAULTS.max_items} notes are processed ${everyWords(PROCESSOR_DEFAULTS.every)}.`);
  return parts.join(" ");
}

type Props = {
  open: boolean;
  onClose: () => void;
  settings: ReadState<NoteSettings>;
  onSave: (patch: Partial<NoteSettings>) => Promise<void>;
  user: string | null;
  onSignOut: () => Promise<void>;
  signingOut: boolean;
  onAppearance: () => void;
};

export function SettingsDrawer(props: Props) {
  const { api } = useApp();
  const [saving, setSaving] = useState<string>("");
  const [saved, setSaved] = useState<string>("");
  const [location, setLocation] = useState(locationEnabled);
  const secure = typeof window === "undefined" || window.isSecureContext !== false;

  const save = async (key: string, patch: Partial<NoteSettings>, confirmation: string) => {
    setSaving(key);
    setSaved("");
    try {
      await props.onSave(patch);
      setSaved(confirmation);
    } catch (e) {
      setSaved(e instanceof ApiError && e.status === 401 ? "Please sign in again." : "That change was not saved.");
    } finally {
      setSaving("");
    }
  };

  return (
    <AfDrawer open={props.open} onClose={props.onClose} label="Settings" title="Settings" width={440} backdrop className="nt-drawer">
      <div className="nt-settings">
        <ReadView read={props.settings} what="your settings">
          {(s) => (
            <>
              <ModelRow label="Filing model" hint="Files and links each note. A small, fast model works well." value={s.filing_model} busy={saving === "filing"} onChange={(v) => void save("filing", { filing_model: v }, "The filing model is saved.")} />
              <ModelRow label="Answering model" hint="Answers your questions in Remember." value={s.answering_model} busy={saving === "answering"} onChange={(v) => void save("answering", { answering_model: v }, "The answering model is saved.")} />
              <AfSwitch
                variant="row"
                label="Instant linking"
                description="Link each note as soon as you send it, instead of shortly after."
                checked={Boolean(s.process_inline_default)}
                busy={saving === "inline"}
                onChange={(next) => void save("inline", { process_inline_default: next }, next ? "Instant linking is on." : "Instant linking is off.")}
              />
              <label className="nt-field">
                <span className="nt-field__label">Look back over my notes</span>
                <select className="nt-input" value={s.reflect_schedule?.every || "7d"} disabled={saving === "reflect"} onChange={(e) => void save("reflect", { reflect_schedule: { every: e.target.value } }, "The schedule is saved.")}>
                  {REFLECT_CHOICES.map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
                <span className="nt-muted">Finds open loops, contradictions and things worth revisiting.</span>
              </label>
            </>
          )}
        </ReadView>
        <AfSwitch
          variant="row"
          label="Location"
          description="Attach where you are to the notes you write in this browser."
          checked={location}
          unavailableReason={secure ? null : "This browser shares your location only over https or on this computer."}
          onChange={(next) => {
            setLocationEnabled(next);
            setLocation(next);
            setSaved(next ? "Location is on for this browser." : "Location is off for this browser.");
          }}
        />
        {saved ? (
          <p className="nt-muted" role="status">
            {saved}
          </p>
        ) : null}
        <QueuePanel open={props.open} api={api} />
        <div className="nt-actions nt-actions--split">
          <button type="button" className="nt-btn" onClick={props.onAppearance}>
            Appearance
          </button>
          <button type="button" className="nt-btn" disabled={props.signingOut} onClick={() => void props.onSignOut()}>
            {props.signingOut ? "Signing out…" : props.user ? `Sign out ${props.user}` : "Sign out"}
          </button>
        </div>
      </div>
    </AfDrawer>
  );
}

function ModelRow({ label, hint, value, onChange, busy }: { label: string; hint: string; value: ModelChoice; onChange: (v: ModelChoice) => void; busy: boolean }) {
  const { api } = useApp();
  const [providers, setProviders] = useState<ProviderOption[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [loadingP, setLoadingP] = useState(true);
  const [loadingM, setLoadingM] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api.get<{ providers?: Array<ProviderOption | string> }>("discovery/providers").then(
      (d) => {
        if (!live) return;
        setProviders((d.providers || []).map((p) => (typeof p === "string" ? { name: p } : p)).filter((p) => p && p.name));
        setLoadingP(false);
      },
      () => {
        if (!live) return;
        setLoadingP(false);
        setError("The list of models could not be loaded.");
      },
    );
    return () => {
      live = false;
    };
  }, [api]);
  useEffect(() => {
    if (!value?.provider) {
      setModels([]);
      return;
    }
    let live = true;
    setLoadingM(true);
    api.get<{ models?: Array<string | { id?: string; name?: string }> }>(`discovery/providers/${encodeURIComponent(value.provider)}/models`).then(
      (d) => {
        if (!live) return;
        setModels((d.models || []).map((m) => (typeof m === "string" ? m : String(m.id || m.name || ""))).filter(Boolean));
        setLoadingM(false);
      },
      () => {
        if (live) setLoadingM(false);
      },
    );
    return () => {
      live = false;
    };
  }, [api, value?.provider]);
  return (
    <div className="nt-field">
      <span className="nt-field__label">{label}</span>
      <ProviderModelSelect
        provider={value?.provider || ""}
        model={value?.model || ""}
        providers={providers}
        models={models}
        onChange={(next) => onChange({ provider: next.provider || null, model: next.model || null })}
        disabled={busy}
        allowGatewayDefault
        gatewayDefaultLabel="Default"
        providerLabel="Provider"
        modelLabel="Model"
        loadingProviders={loadingP}
        loadingModels={loadingM}
        providerError={error || undefined}
        layout="stack"
      />
      <span className="nt-muted">{hint}</span>
    </div>
  );
}

function QueuePanel({ open, api }: { open: boolean; api: ReturnType<typeof useApp>["api"] }) {
  const queue = useRead<QueueInfo>(() => (open ? api.get<QueueInfo>("note/queue") : new Promise<QueueInfo>(() => undefined)), [api, open]);
  const [busy, setBusy] = useState("");
  const [line, setLine] = useState("");
  const processNow = async () => {
    setBusy("run");
    setLine("");
    try {
      let id = queue.data?.automations?.processor_id || "";
      if (!id) {
        const status = await api.get<NoteStatus>("note/status");
        id = status.automations?.processor_id || "";
      }
      if (!id) {
        const ensured = await api.post<{ processor_id?: string }>("note/processor/ensure");
        id = ensured.processor_id || "";
      }
      if (!id) throw new ApiError(500, "no processor", "no_processor");
      await api.post(`automations/${encodeURIComponent(id)}/commands`, { command_id: randomId(), type: "automation.run_now" });
      setLine("Processing started.");
      queue.reload();
    } catch (e) {
      setLine(e instanceof ApiError && e.status === 409 ? "Already running." : errorSentence(e, "the queue").replace("could not be loaded", "could not be started"));
    } finally {
      setBusy("");
    }
  };
  const retry = async (noteId: string) => {
    setBusy(noteId);
    setLine("");
    try {
      await api.post(`note/queue/${encodeURIComponent(noteId)}/retry`);
      setLine("The note is back in the queue.");
      queue.reload();
    } catch {
      setLine("That note could not be queued again.");
    } finally {
      setBusy("");
    }
  };
  return (
    <section className="nt-queue" aria-label="Queue">
      <h3>Queue</h3>
      <ReadView read={queue} what="the queue">
        {(q) => (
          <>
            <p>{queueSentence(q)}</p>
            {q.failed.length ? (
              <ul className="nt-list">
                {q.failed.map((n) => (
                  <li key={n.note_id} className="nt-list__row">
                    <div>
                      <NoteLink note={{ note_id: n.note_id, title: n.title || "A note", occurred_at: n.occurred_at }} />
                      {n.last_error ? <p className="nt-error-text">{n.last_error}</p> : null}
                    </div>
                    <button type="button" className="nt-btn nt-btn--quiet" disabled={busy === n.note_id} onClick={() => void retry(n.note_id)}>
                      {busy === n.note_id ? "Retrying…" : "Retry"}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="nt-actions">
              <button type="button" className="nt-btn" disabled={busy === "run"} onClick={() => void processNow()}>
                {busy === "run" ? "Starting…" : "Process now"}
              </button>
            </div>
          </>
        )}
      </ReadView>
      {line ? (
        <p className="nt-muted" role="status">
          {line}
        </p>
      ) : null}
    </section>
  );
}
