import { useState } from "react";
import { AfModal } from "@abstractframework/ui-kit";
import { Markdown } from "@abstractframework/panel-chat";
import { ApiError } from "../lib/api";
import { useRead } from "../lib/hooks";
import type { ClaimChain, Note, RelationTimeline } from "../lib/types";
import { capitalize, claimSentence, longDate, predicateWords, shortDate, topicLabel } from "../lib/words";
import { EntityChip, KindBadge, NoteLink, ReadView, openClaim, overlayStore, useApp, useOverlay } from "./common";
import { ThenNowCard } from "./ThenNow";

export function Dialogs() {
  const [overlay] = useOverlay();
  const close = () => overlayStore.set(null);
  if (!overlay) return null;
  if (overlay.type === "note") return <NoteDialog id={overlay.id} onClose={close} />;
  if (overlay.type === "claim") return <ClaimDialog id={overlay.id} onClose={close} />;
  return <RelationDialog a={overlay.a} b={overlay.b} onClose={close} />;
}

function NoteDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { api } = useApp();
  const read = useRead<Note>(() => api.get<Note>(`note/notes/${encodeURIComponent(id)}`), [api, id]);
  return (
    <AfModal open onClose={onClose} title={read.data?.title || "Note"} size="narrow">
      <ReadView read={read} what="this note">
        {(n) => (
          <div className="nt-dialog">
            <p className="nt-meta">
              <KindBadge kind={n.kind} /> {longDate(n.occurred_at)}
              {n.location?.label ? ` · ${n.location.label}` : ""}
              {n.source?.client ? ` · from ${n.source.client === "web" ? "the web" : `the ${n.source.client}`}` : ""}
            </p>
            <div className="nt-note-text">
              <Markdown text={n.text || ""} images="link" />
            </div>
            {(n.topics || []).length ? <p className="nt-muted">{(n.topics || []).map((t) => `#${topicLabel(t)}`).join("  ")}</p> : null}
            {(n.attachments || []).length ? (
              <div className="nt-block">
                <h3>Files</h3>
                <ul className="nt-list">
                  {(n.attachments || []).map((a) => (
                    <li key={a.attachment_id} className="nt-list__row">
                      <a className="nt-link" href={api.url(`note/notes/${encodeURIComponent(n.note_id)}/attachments/${encodeURIComponent(a.attachment_id)}`)} target="_blank" rel="noopener noreferrer">
                        <span className="nt-link__title">{a.filename || "File"}</span>
                        {a.size ? <span className="nt-link__date">{Math.max(1, Math.round(a.size / 1024))} KB</span> : null}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {(n.entities || []).length ? (
              <div className="nt-block">
                <h3>Linked to</h3>
                <div className="nt-chips">
                  {(n.entities || []).map((e) => (
                    <EntityChip key={e.entity_id} entity={e} />
                  ))}
                </div>
              </div>
            ) : null}
            {n.revision ? (
              <div className="nt-block">
                <h3>What it changed</h3>
                <p>{capitalize(n.revision.summary)}</p>
              </div>
            ) : n.status === "queued" ? (
              <p className="nt-muted">This note is waiting to be processed.</p>
            ) : null}
            {(n.claims || []).length ? (
              <div className="nt-block">
                <h3>What it says</h3>
                <ul className="nt-list">
                  {(n.claims || []).map((c) => (
                    <li key={c.claim_id} className="nt-list__row">
                      <span className={c.status !== "active" ? "nt-struck" : ""}>{claimSentence(c)}</span>
                      <button type="button" className="nt-btn nt-btn--quiet" onClick={() => openClaim(c.claim_id)}>
                        Why
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {(n.revisits || []).length ? (
              <div className="nt-block">
                <h3>You noted this before</h3>
                {(n.revisits || []).map((r) => (
                  <NoteLink key={r.note_id} note={{ note_id: r.note_id, title: r.title || "Earlier note", occurred_at: r.occurred_at }} />
                ))}
              </div>
            ) : null}
          </div>
        )}
      </ReadView>
    </AfModal>
  );
}

/** Why do I believe this: the claim, what it replaced or what replaced it, the notes and excerpts behind it, and "This is wrong". */
function ClaimDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { api } = useApp();
  const read = useRead<ClaimChain>(() => api.get<ClaimChain>(`note/claims/${encodeURIComponent(id)}`), [api, id]);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [line, setLine] = useState("");
  const retract = async () => {
    setBusy(true);
    setLine("");
    try {
      await api.post(`note/claims/${encodeURIComponent(id)}/retract`, { reason: "Marked wrong in the web app." });
      setConfirming(false);
      setLine("Withdrawn. Your notebook no longer believes this.");
      read.reload();
    } catch (e) {
      setLine(e instanceof ApiError && e.status === 401 ? "Please sign in again." : "This could not be withdrawn.");
    } finally {
      setBusy(false);
    }
  };
  const c = read.data?.claim;
  const footer = c && c.status === "active" ? (
    confirming ? (
      <div className="nt-confirm" role="group" aria-label="Confirm">
        <span>Withdraw this from your notebook? The notes stay.</span>
        <button type="button" className="nt-btn" onClick={() => setConfirming(false)} disabled={busy}>
          Keep it
        </button>
        <button type="button" className="nt-btn nt-btn--danger" onClick={() => void retract()} disabled={busy}>
          {busy ? "Withdrawing…" : "Withdraw"}
        </button>
      </div>
    ) : (
      <button type="button" className="nt-btn nt-btn--danger" onClick={() => setConfirming(true)}>
        This is wrong
      </button>
    )
  ) : null;
  return (
    <AfModal open onClose={onClose} title="Why do I believe this" size="narrow" footer={footer}>
      <ReadView read={read} what="this">
        {(chain) => (
          <div className="nt-dialog">
            <p className="nt-claim">
              <strong>{claimSentence(chain.claim)}</strong>
            </p>
            <p className="nt-meta">
              {chain.claim.status === "active" ? "Believed" : chain.claim.status === "retracted" ? "Withdrawn" : "No longer believed"} · since {longDate(chain.claim.valid_from || chain.claim.observed_at)}
              {chain.claim.valid_until ? ` until ${longDate(chain.claim.valid_until)}` : ""}
              {chain.claim.certainty && chain.claim.certainty !== "stated" ? ` · ${chain.claim.certainty}` : ""}
            </p>
            {chain.superseded_by ? (
              <div className="nt-block">
                <h3>Replaced by</h3>
                <button type="button" className="nt-link" onClick={() => openClaim(chain.superseded_by!.claim_id)}>
                  <span className="nt-link__title">{claimSentence(chain.superseded_by)}</span>
                  <span className="nt-link__date">{shortDate(chain.superseded_by.observed_at)}</span>
                </button>
              </div>
            ) : null}
            {chain.supersedes.length ? (
              <div className="nt-block">
                <h3>It replaced</h3>
                {chain.supersedes.map((s) => (
                  <button key={s.claim_id} type="button" className="nt-link" onClick={() => openClaim(s.claim_id)}>
                    <span className="nt-link__title nt-struck">{claimSentence(s)}</span>
                    <span className="nt-link__date">{shortDate(s.observed_at)}</span>
                  </button>
                ))}
              </div>
            ) : null}
            <div className="nt-block">
              <h3>From your notes</h3>
              {chain.notes.map((n) => (
                <div key={n.note_id} className="nt-evidence">
                  <NoteLink note={n} />
                  {n.excerpt ? <blockquote>{n.excerpt}</blockquote> : null}
                </div>
              ))}
              {chain.attachments.map((a) => (
                <div key={a.attachment_id} className="nt-evidence">
                  <span className="nt-muted">{a.filename || "File"}</span>
                  {a.excerpt ? <blockquote>{a.excerpt}</blockquote> : null}
                </div>
              ))}
            </div>
            {line ? (
              <p className="nt-muted" role="status">
                {line}
              </p>
            ) : null}
          </div>
        )}
      </ReadView>
    </AfModal>
  );
}

/** The relationship between two entities through time. */
function RelationDialog({ a, b, onClose }: { a: string; b: string; onClose: () => void }) {
  const { api } = useApp();
  const read = useRead<RelationTimeline>(() => api.get<RelationTimeline>(`note/relations/${encodeURIComponent(a)}/${encodeURIComponent(b)}`), [api, a, b]);
  const title = read.data ? `${read.data.a.name} and ${read.data.b.name}` : "How these two are linked";
  return (
    <AfModal open onClose={onClose} title={title} size="narrow">
      <ReadView read={read} what="this link">
        {(r) => (
          <div className="nt-dialog">
            {r.then_now && (r.then_now.earliest || r.then_now.latest) ? <ThenNowCard data={r.then_now} title="Then and now" /> : null}
            <div className="nt-block">
              <h3>Over time</h3>
              {r.claims.length ? (
                <ol className="nt-timeline">
                  {r.claims.map((c) => (
                    <li key={c.claim_id} className={c.status !== "active" ? "nt-timeline__item nt-timeline__item--old" : "nt-timeline__item"}>
                      <span className="nt-timeline__date">{shortDate(c.observed_at)}</span>
                      <button type="button" className="nt-link" onClick={() => openClaim(c.claim_id)}>
                        <span className="nt-link__title">{`${c.subject?.name || ""} ${predicateWords(c.predicate)} ${c.object?.name || c.object_literal || ""}`}</span>
                      </button>
                      {c.status !== "active" ? <span className="nt-muted"> · {c.status === "retracted" ? "withdrawn" : "replaced"}</span> : null}
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="nt-muted">They appear in the same notes but nothing links them directly.</p>
              )}
            </div>
            {(r.notes || []).length ? (
              <div className="nt-block">
                <h3>Notes about both</h3>
                {(r.notes || []).map((n) => (
                  <NoteLink key={n.note_id} note={n} />
                ))}
              </div>
            ) : null}
          </div>
        )}
      </ReadView>
    </AfModal>
  );
}
