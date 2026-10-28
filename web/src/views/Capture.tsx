import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChatComposer, DragPresence, Markdown, dragCarriesFiles, droppedFiles, folderRefusal, pastedFiles } from "@abstractframework/panel-chat";
import { AfChipButton, Icon, randomId } from "@abstractframework/ui-kit";
import { ApiError, errorSentence } from "../lib/api";
import { ACK_ICONS, ACK_WORDS, ackState, ackSummary, pendingLine } from "../lib/ack";
import { runCapture, tooLarge, watchNote, type CaptureFile, type CaptureItem } from "../lib/capture";
import { THREAD_KEY, readLocal, writeLocal } from "../lib/local";
import { currentPlace } from "../lib/location";
import { navigate, type Route } from "../lib/nav";
import type { About, AboutKind } from "../lib/types";
import { capitalize, entryTime, exactTime, longDate, sizeWords, topicLabel } from "../lib/words";
import { EntityChip, KindBadge, openNote, useApp } from "../components/common";

const MAX_KEPT = 40;

function loadThread(): CaptureItem[] {
  try {
    const raw = readLocal(THREAD_KEY);
    const items = raw ? (JSON.parse(raw) as CaptureItem[]) : [];
    return Array.isArray(items) ? items.slice(-MAX_KEPT) : [];
  } catch {
    return [];
  }
}

const ABOUT_KINDS: AboutKind[] = ["insight", "task", "cue", "entity", "note"];

/** `#/capture?about=<id>&kind=<insight|task|cue|entity|note>&title=<title>` -> the note's subject. */
export function aboutFromParams(params: URLSearchParams): About | null {
  const id = params.get("about") || "";
  const kind = params.get("kind") as AboutKind;
  if (!id || !ABOUT_KINDS.includes(kind)) return null;
  return { kind, id, title: params.get("title") || "" };
}

/** A preview for an image the person picked (this page only; a reload shows the file name). */
function previewOf(f: File): string | null {
  if (!f.type.startsWith("image/") || typeof URL === "undefined" || !URL.createObjectURL) return null;
  try {
    return URL.createObjectURL(f);
  } catch {
    return null;
  }
}

export function CaptureView({ route, processInline }: { route: Route; processInline: boolean }) {
  const { api, sessionId } = useApp();
  const [items, setItems] = useState<CaptureItem[]>(loadThread);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [notice, setNotice] = useState("");
  const [dragging, setDragging] = useState(false);
  const filesFor = useRef(new Map<string, File[]>());
  const previewsFor = useRef(new Map<string, Array<string | null>>());
  const fileInput = useRef<HTMLInputElement>(null);
  const stream = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const pastedText = useRef(false);
  const alive = useRef(true);
  // Closes the runs' live views when the page goes away (`update` is a no-op by then).
  const leaving = useRef<AbortController>(new AbortController());
  const inlineRef = useRef(processInline);
  inlineRef.current = processInline;

  const about = aboutFromParams(route.params);

  useEffect(() => {
    alive.current = true;
    leaving.current = new AbortController();
    const previews = previewsFor.current;
    return () => {
      alive.current = false;
      leaving.current.abort();
      for (const urls of previews.values()) for (const u of urls) if (u) URL.revokeObjectURL(u);
    };
  }, []);

  useEffect(() => {
    writeLocal(THREAD_KEY, JSON.stringify(items.slice(-MAX_KEPT).map((i) => ({ ...i }))));
  }, [items]);

  // The stream follows the newest note unless the person scrolled up to read.
  useLayoutEffect(() => {
    const el = stream.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [items]);

  const update = useCallback((id: string, patch: Partial<CaptureItem>) => {
    if (!alive.current) return;
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)));
  }, []);

  const deps = useMemo(() => ({ api, sessionId, processInline: () => inlineRef.current, update, alive: () => alive.current, signal: () => leaving.current.signal }), [api, sessionId, update]);

  // A reload resumes watching the notes that were still being organized.
  useEffect(() => {
    for (const it of loadThread()) {
      if ((it.state === "queued" || it.state === "saving") && it.noteId) void watchNote(it.id, it.noteId, deps, (ms) => new Promise((r) => setTimeout(r, ms)), () => alive.current);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const addFiles = useCallback((incoming: File[]) => {
    if (!incoming.length) return;
    if (tooLarge(incoming)) {
      setNotice(errorSentence(new ApiError(413, "", "too_large")));
      const ok = incoming.filter((f) => !tooLarge([f]));
      if (ok.length) setFiles((prev) => [...prev, ...ok]);
      return;
    }
    setNotice("");
    setFiles((prev) => [...prev, ...incoming]);
  }, []);

  // Previews of the files waiting in the composer.
  const pendingPreviews = useMemo(() => files.map(previewOf), [files]);
  useEffect(() => () => pendingPreviews.forEach((u) => u && URL.revokeObjectURL(u)), [pendingPreviews]);

  // Drop anywhere on the capture view.
  useEffect(() => {
    const presence = new DragPresence();
    const enter = (e: DragEvent) => {
      if (!dragCarriesFiles(e.dataTransfer?.types)) return;
      e.preventDefault();
      presence.enter(Date.now());
      setDragging(true);
    };
    const over = (e: DragEvent) => {
      if (!dragCarriesFiles(e.dataTransfer?.types)) return;
      e.preventDefault();
      presence.over(Date.now());
    };
    const leave = () => {
      if (!presence.leave(Date.now())) setDragging(false);
    };
    const drop = (e: DragEvent) => {
      if (!dragCarriesFiles(e.dataTransfer?.types)) return;
      e.preventDefault();
      presence.reset();
      setDragging(false);
      const d = droppedFiles(e.dataTransfer);
      if (d.folders.length) setNotice(folderRefusal(d.folders));
      addFiles(d.files);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [addFiles]);

  const send = useCallback(async () => {
    const body = text.trim();
    if (!body && !files.length) return;
    const place = await currentPlace();
    const item: CaptureItem = {
      id: randomId(),
      key: randomId(),
      text: body,
      files: files.map((f) => ({ name: f.name, size: f.size, type: f.type, lastModified: f.lastModified })),
      artifacts: [],
      occurred_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
      location: place,
      about,
      pasted: pastedText.current,
      state: files.length ? "uploading" : "saving",
    };
    pastedText.current = false;
    filesFor.current.set(item.id, files);
    // The composer's previews move to the note (not revoked: the stream shows them now).
    previewsFor.current.set(item.id, files.map(previewOf));
    stick.current = true;
    setItems((prev) => [...prev.slice(-(MAX_KEPT - 1)), item]);
    setText("");
    setFiles([]);
    setNotice("");
    if (about) navigate("capture");
    await runCapture(item, files, deps);
  }, [text, files, about, deps]);

  const retry = useCallback(
    (it: CaptureItem) => {
      const fs = filesFor.current.get(it.id) || [];
      if (it.files.length && it.artifacts.length < it.files.length && fs.length < it.files.length) {
        update(it.id, { error: "Add the files again; this page no longer has them." });
        return;
      }
      // Same idempotency key: a retry never saves the note twice.
      void runCapture(it, fs, deps);
    },
    [deps, update],
  );

  return (
    <div className="nt-capture">
      <div
        ref={stream}
        className="nt-stream"
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        {items.length ? (
          <ol className="nt-stream__inner" aria-label="Your notes">
            {items.map((it) => (
              <li key={it.id}>
                <Entry item={it} previews={previewsFor.current.get(it.id) || []} onRetry={() => retry(it)} />
              </li>
            ))}
          </ol>
        ) : (
          <div className="nt-empty">
            <h1>What is on your mind?</h1>
            <p>Write your first note below.</p>
          </div>
        )}
      </div>
      <div className="nt-composer">
        <div className="nt-composer__inner">
          {about ? (
            <div className="nt-about" role="status">
              <span>
                {about.kind === "cue" ? "Answering" : "A note about"} <strong>{about.title}</strong>
              </span>
              <button type="button" className="nt-btn nt-btn--quiet" onClick={() => navigate("capture")}>
                Cancel
              </button>
            </div>
          ) : null}
          {notice ? (
            <p className="nt-notice" role="alert">
              {notice}
            </p>
          ) : null}
          <ChatComposer
            value={text}
            onChange={setText}
            onSubmit={() => void send()}
            placeholder={about ? "Write your answer…" : "Write a note…"}
            ariaLabel="Note"
            sendLabel="Save"
            autoFocus
            onPaste={(e) => {
              const pasted = pastedFiles(e.clipboardData);
              if (pasted.length) {
                e.preventDefault();
                addFiles(pasted);
              } else if (e.clipboardData?.getData("text")) {
                pastedText.current = true; // a pasted document may carry its own date
              }
            }}
            leading={
              files.length ? (
                <ul className="nt-pending" aria-label="Files to add">
                  {files.map((f, i) => (
                    <FileChip key={`${f.name}:${i}`} file={{ name: f.name, size: f.size, type: f.type }} preview={pendingPreviews[i]} onRemove={() => setFiles((prev) => prev.filter((_, j) => j !== i))} />
                  ))}
                </ul>
              ) : null
            }
            actions={
              <>
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  hidden
                  onChange={(e) => {
                    addFiles(Array.from(e.target.files || []));
                    e.target.value = "";
                  }}
                />
                <button type="button" className="nt-icon-btn" aria-label="Add files" title="Add files" onClick={() => fileInput.current?.click()}>
                  <Icon name="paperclip" size={18} />
                </button>
              </>
            }
            overlay={dragging ? <div className="nt-drop">Drop to add to this note</div> : null}
          />
          <p className="nt-composer__hint">
            <span>
              <kbd>Enter</kbd> saves · <kbd>Shift</kbd> + <kbd>Enter</kbd> starts a new line
            </span>
            <span>Paste or drop files to attach them</span>
          </p>
        </div>
      </div>
    </div>
  );
}

function fileIcon(type: string): "image" | "video" | "music" | "file" {
  if (type.startsWith("image/")) return "image";
  if (type.startsWith("video/")) return "video";
  if (type.startsWith("audio/")) return "music";
  return "file";
}

function FileChip({ file, preview, onRemove }: { file: CaptureFile; preview?: string | null; onRemove?: () => void }) {
  return (
    <li className="nt-file">
      {preview ? (
        <img className="nt-file__thumb" src={preview} alt="" />
      ) : (
        <span className="nt-file__thumb" aria-hidden="true">
          <Icon name={fileIcon(file.type)} size={18} />
        </span>
      )}
      <span className="nt-file__name" title={file.name}>
        {file.name}
      </span>
      <span className="nt-file__size">{sizeWords(file.size)}</span>
      {onRemove ? (
        <button type="button" className="nt-icon-btn nt-file__remove" aria-label={`Remove ${file.name}`} title={`Remove ${file.name}`} onClick={onRemove}>
          <Icon name="x" size={14} />
        </button>
      ) : null}
    </li>
  );
}

/** One captured note: the original as written, then its acknowledgement. */
function Entry({ item, previews, onRetry }: { item: CaptureItem; previews: Array<string | null>; onRetry: () => void }) {
  return (
    <article className="nt-entry" aria-label={`Note written ${exactTime(item.occurred_at)}`}>
      <div className="nt-entry__card">
        <p className="nt-entry__meta">
          <time dateTime={item.occurred_at} title={exactTime(item.occurred_at)}>
            {entryTime(item.occurred_at)}
          </time>
          {item.about ? <span>{`· ${item.about.kind === "cue" ? "answering" : "about"} ${item.about.title}`}</span> : null}
        </p>
        {item.text ? (
          <div className="nt-entry__text">
            <Markdown text={item.text} images="link" />
          </div>
        ) : null}
        {item.files.length ? (
          <ul className="nt-files" aria-label="Files">
            {item.files.map((f, i) => (
              <FileChip key={`${f.name}:${i}`} file={f} preview={previews[i]} />
            ))}
          </ul>
        ) : null}
      </div>
      <Ack item={item} onRetry={onRetry} />
    </article>
  );
}

/** The acknowledgement: one state, one short line; it opens on what was filed and linked. */
function Ack({ item, onRetry }: { item: CaptureItem; onRetry: () => void }) {
  const state = ackState(item);
  const [open, setOpen] = useState(false);
  const note = item.note;
  const organized = state === "organized";
  const summary = organized ? ackSummary(note, item.state === "duplicate") : state === "attention" ? item.error || "This note could not be saved." : pendingLine(item);
  const expandable = Boolean(note || item.answer);
  const panel = `ack-${item.id}`;
  const revisits = (note?.revisits || []).filter((r) => r.note_id !== note?.note_id);
  const changed = note?.revision?.summary || item.revisionSummary || "";
  return (
    <div className={`nt-ack${organized ? " nt-ack--organized" : ""}`} data-state={state} data-answer-source={item.answerSource || ""} aria-live="polite">
      {organized && note ? (
        // What the eye lands on first: what the note became and what it links to.
        <div className="nt-ack__lead">
          <button type="button" className="nt-link nt-ack__title" onClick={() => openNote(note.note_id)}>
            <span className="nt-link__title">{note.title}</span>
          </button>
          {(note.entities || []).length ? (
            <span className="nt-chips" aria-label="Linked to">
              {(note.entities || []).map((e) => (
                <EntityChip key={e.entity_id} entity={e} />
              ))}
            </span>
          ) : null}
        </div>
      ) : null}
      <div className="nt-ack__row">
        <button
          type="button"
          className="nt-ack__line"
          aria-expanded={expandable ? open : undefined}
          aria-controls={expandable && open ? panel : undefined}
          aria-disabled={expandable ? undefined : true}
          onClick={() => expandable && setOpen(!open)}
        >
          <span className={`nt-state nt-state--${state}`}>
            <Icon name={ACK_ICONS[state]} size={14} className={state === "sending" ? "nt-spin" : undefined} />
            <span>{ACK_WORDS[state]}</span>
          </span>
          {summary ? <span className="nt-ack__summary">{summary}</span> : null}
          {expandable ? <Icon name="chevronRight" size={14} className="nt-ack__chev" /> : null}
        </button>
        {state === "attention" && !item.noteId ? (
          <button type="button" className="nt-btn nt-btn--quiet nt-btn--sm" onClick={onRetry}>
            Retry
          </button>
        ) : null}
      </div>
      {expandable && open ? (
        <div id={panel} className="nt-ack__more">
          {note && !organized ? (
            <div className="nt-ack__row">
              <KindBadge kind={note.kind} />
              <button type="button" className="nt-link" onClick={() => openNote(note.note_id)}>
                <span className="nt-link__title">{note.title}</span>
              </button>
            </div>
          ) : null}
          {!organized && item.answer ? <p className="nt-ack__reply">{item.answer}</p> : null}
          {organized && (note?.topics || []).length ? (
            <div className="nt-ack__row" aria-label="Topics">
              {(note?.topics || []).map((t) => (
                <AfChipButton key={t} size="sm" tone="muted" title="Find notes with this topic" onClick={() => navigate("remember", { topic: t })}>
                  {`#${topicLabel(t)}`}
                </AfChipButton>
              ))}
            </div>
          ) : null}

          {organized && revisits[0] ? (
            <p className="nt-muted">
              {"You noted this before on "}
              <button type="button" className="nt-link" onClick={() => openNote(revisits[0].note_id)}>
                <span className="nt-link__title">{longDate(revisits[0].occurred_at)}</span>
              </button>
              {revisits.length > 1 ? ` and ${revisits.length - 1} more ${revisits.length === 2 ? "time" : "times"}` : ""}.
            </p>
          ) : null}
          {organized && changed ? <p className="nt-muted">{capitalize(changed)}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
