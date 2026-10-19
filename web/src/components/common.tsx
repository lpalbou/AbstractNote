import React, { createContext, useContext } from "react";
import { AfChipButton, Icon } from "@abstractframework/ui-kit";
import type { Api } from "../lib/api";
import { errorSentence } from "../lib/api";
import { createStore, useStore, type ReadState } from "../lib/hooks";
import { navigate } from "../lib/nav";
import { sparkline } from "../lib/sparkline";
import type { EntityRef } from "../lib/types";
import { capitalize, shortDate } from "../lib/words";

export type AppContextValue = { api: Api; sessionId: string };
export const AppContext = createContext<AppContextValue | null>(null);
export function useApp(): AppContextValue {
  const v = useContext(AppContext);
  if (!v) throw new Error("useApp outside AppContext");
  return v;
}

/** What the dialog layer shows (one at a time). */
export type Overlay = { type: "note"; id: string } | { type: "claim"; id: string } | { type: "relation"; a: string; b: string } | null;
export const overlayStore = createStore<Overlay>(null);
export const useOverlay = () => useStore(overlayStore);
export const openNote = (id: string) => overlayStore.set({ type: "note", id });
export const openClaim = (id: string) => overlayStore.set({ type: "claim", id });
export const openRelation = (a: string, b: string) => overlayStore.set({ type: "relation", a, b });
export const openEntity = (id: string) => {
  overlayStore.set(null);
  navigate("graph", { focus: id, entity: id });
};

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="nt-loading" role="status" aria-live="polite">
      <Icon name="loader" size={16} className="nt-spin" />
      <span>{label}</span>
    </div>
  );
}

/** Spinner until the read answered; one sentence + Retry once every try failed (ADR-0039). */
export function ReadView<T>({ read, what, children, empty }: { read: ReadState<T>; what: string; children: (data: T) => React.ReactNode; empty?: (data: T) => boolean | string }) {
  if (read.loading && read.data === null) return <Spinner />;
  if (read.data === null) {
    return (
      <div className="nt-error" role="alert">
        <span>{errorSentence(read.error, what)}</span>
        <button type="button" className="nt-btn nt-btn--quiet" onClick={read.reload}>
          Retry
        </button>
      </div>
    );
  }
  const e = empty ? empty(read.data) : false;
  if (e) return <p className="nt-muted">{typeof e === "string" ? e : "Nothing here yet."}</p>;
  return <>{children(read.data)}</>;
}

export function EntityChip({ entity, size = "sm" }: { entity: EntityRef; size?: "sm" | "md" }) {
  return (
    <AfChipButton size={size} tone="info" title={`${capitalize(entity.kind)} · open`} onClick={() => openEntity(entity.entity_id)}>
      {entity.name}
    </AfChipButton>
  );
}

export function NoteLink({ note, showDate = true }: { note: { note_id: string; title: string; occurred_at?: string }; showDate?: boolean }) {
  return (
    <button type="button" className="nt-link" onClick={() => openNote(note.note_id)}>
      <span className="nt-link__title">{note.title || "Untitled note"}</span>
      {showDate && note.occurred_at ? <span className="nt-link__date">{shortDate(note.occurred_at)}</span> : null}
    </button>
  );
}

export function KindBadge({ kind }: { kind: string }) {
  return <span className={`nt-kind nt-kind--${kind}`}>{kind}</span>;
}

export function Section({ title, sub, children, actions, id, className }: { title: string; sub?: string; children: React.ReactNode; actions?: React.ReactNode; id?: string; className?: string }) {
  return (
    <section className={`nt-section${className ? ` ${className}` : ""}`} aria-labelledby={id ? `${id}-h` : undefined}>
      <header className="nt-section__head">
        <h2 id={id ? `${id}-h` : undefined}>
          {title}
          {sub ? <span className="nt-section__sub">{sub}</span> : null}
        </h2>
        {actions ? <div className="nt-section__actions">{actions}</div> : null}
      </header>
      {children}
    </section>
  );
}

/** A disclosure line ("Why this surfaced"): a button with a turning chevron; the panel is the caller's. */
export function Disclose({ open, onToggle, controls, children }: { open: boolean; onToggle: () => void; controls: string; children: React.ReactNode }) {
  return (
    <button type="button" className="nt-disclose" aria-expanded={open} aria-controls={controls} onClick={onToggle}>
      <Icon name="chevronRight" size={12} />
      <span>{children}</span>
    </button>
  );
}

/** A small activity sparkline from note dates (notes per week over the shown range). */
export function Sparkline({ dates, from, to, label, width = 120, height = 26 }: { dates: string[]; from: number; to: number; label: string; width?: number; height?: number }) {
  const weeks = Math.max(4, Math.min(52, Math.round((to - from) / (7 * 86400_000))));
  const s = sparkline(dates, { from, to, buckets: weeks, width, height });
  return (
    <svg className="nt-spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label}: ${dates.length} notes, at most ${s.max} in one week`}>
      <path d={s.area} className="nt-spark__area" />
      <path d={s.path} className="nt-spark__line" />
    </svg>
  );
}
