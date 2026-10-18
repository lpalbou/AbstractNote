import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChatComposer, ChatThread, DragPresence, Markdown, dragCarriesFiles, droppedFiles, folderRefusal, pastedFiles, type ChatMessage } from "@abstractframework/panel-chat";
import { AfChip, AfChipButton, Icon, randomId } from "@abstractframework/ui-kit";
import { ApiError, errorSentence } from "../lib/api";
import { runCapture, tooLarge, watchNote, type CaptureItem } from "../lib/capture";
import { THREAD_KEY, readLocal, writeLocal } from "../lib/local";
import { currentPlace } from "../lib/location";
import { navigate, type Route } from "../lib/nav";
import type { About, AboutKind } from "../lib/types";
import { capitalize, longDate, topicLabel } from "../lib/words";
import { EntityChip, openNote, useApp } from "../components/common";

const MARK = /\n?<!--nt:([A-Za-z0-9-]+)-->$/;
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

function stateLine(item: CaptureItem): string {
  switch (item.state) {
    case "uploading":
      return item.files.length > 1 ? "Adding your files…" : "Adding your file…";
    case "saving":
      return "Saving…";
    case "queued":
      return "Saved. The links come when it is processed.";
    case "processed":
      return "";
    case "duplicate":
      return "";
    case "failed":
      return item.error || "This note could not be saved.";
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
  const fileInput = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  const inlineRef = useRef(processInline);
  inlineRef.current = processInline;

  const about = aboutFromParams(route.params);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    writeLocal(THREAD_KEY, JSON.stringify(items.slice(-MAX_KEPT).map((i) => ({ ...i }))));
  }, [items]);

  const update = useCallback((id: string, patch: Partial<CaptureItem>) => {
    if (!alive.current) return;
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)));
  }, []);

  const deps = useMemo(() => ({ api, sessionId, processInline: () => inlineRef.current, update, alive: () => alive.current }), [api, sessionId, update]);

  // A reload resumes watching the notes that were still queued.
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
      files: files.map((f) => ({ name: f.name, size: f.size, type: f.type })),
      artifacts: [],
      occurred_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
      location: place,
      about,
      state: files.length ? "uploading" : "saving",
    };
    filesFor.current.set(item.id, files);
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

  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);

  const messages: ChatMessage[] = useMemo(() => {
    const out: ChatMessage[] = [];
    for (const it of items) {
      out.push({
        id: `u:${it.id}`,
        role: "user",
        content: it.text || (it.files.length ? "(files)" : ""),
        ts: it.occurred_at,
        attachments: it.files.map((f, i) => ({ id: `${it.id}:f${i}`, label: f.name, title: `${f.name} · ${Math.max(1, Math.round(f.size / 1024))} KB`, disabled: true })),
      });
      const pending = it.state === "uploading" || (it.state === "saving" && !it.answer);
      out.push({
        id: `a:${it.id}`,
        role: "assistant",
        title: "Notebook",
        content: `${it.answer || ""}\n<!--nt:${it.id}-->`,
        ts: it.occurred_at,
        level: it.state === "failed" ? "error" : undefined,
        live: pending ? { callId: it.id, reasoning: "", label: it.state === "uploading" ? "adding files" : "saving" } : undefined,
      });
    }
    return out;
  }, [items]);

  const renderMarkdown = useCallback(
    (md: string) => {
      const m = md.match(MARK);
      const it = m ? byId.get(m[1]) : undefined;
      const body = md.replace(MARK, "");
      return (
        <div className="nt-answer">
          {body.trim() ? <Markdown text={body} images="link" /> : null}
          {it ? <Filing item={it} onRetry={() => retry(it)} /> : null}
        </div>
      );
    },
    [byId, retry],
  );

  return (
    <div className="nt-capture">
      <ChatThread
        className="nt-thread"
        messages={messages}
        messageProps={{ renderMarkdown, showCopy: false }}
        empty={
          <div className="nt-empty">
            <h2>Write it down</h2>
            <p>Type a thought, paste a link, or drop a file. You will see what was saved, how it was filed, and what it connects to.</p>
          </div>
        }
      />
      <div className="nt-composer">
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
            }
          }}
          leading={
            files.length ? (
              <div className="nt-files">
                {files.map((f, i) => (
                  <AfChip key={`${f.name}:${i}`} size="sm" onRemove={() => setFiles((prev) => prev.filter((_, j) => j !== i))} removeLabel={`Remove ${f.name}`}>
                    {f.name}
                  </AfChip>
                ))}
              </div>
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
      </div>
    </div>
  );
}

/** What was saved and how it was filed, with links into Graph and Remember. */
function Filing({ item, onRetry }: { item: CaptureItem; onRetry: () => void }) {
  const note = item.note;
  const line = stateLine(item);
  const revisits = (note?.revisits || []).filter((r) => r.note_id !== note?.note_id);
  const revisit = revisits[0];
  const entities = note?.entities || [];
  const summary = note?.revision?.summary || item.revisionSummary || "";
  return (
    <div className="nt-filing" data-state={item.state} data-answer-source={item.answerSource || ""}>
      {note ? (
        <div className="nt-filing__row">
          <span className={`nt-kind nt-kind--${note.kind}`}>{note.kind}</span>
          <button type="button" className="nt-link" onClick={() => openNote(note.note_id)}>
            <span className="nt-link__title">{note.title}</span>
          </button>
          {(note.topics || []).map((t) => (
            <AfChipButton key={t} size="sm" tone="muted" title="Find notes with this topic" onClick={() => navigate("remember", { topic: t })}>
              {`#${topicLabel(t)}`}
            </AfChipButton>
          ))}
        </div>
      ) : null}
      {revisit ? (
        <p className="nt-filing__revisit">
          You noted this on{" "}
          <button type="button" className="nt-link" onClick={() => openNote(revisit.note_id)}>
            <span className="nt-link__title">{longDate(revisit.occurred_at)}</span>
          </button>
          {revisits.length > 1 ? ` and ${revisits.length - 1} more ${revisits.length === 2 ? "time" : "times"}` : ""}.
        </p>
      ) : null}
      {entities.length ? (
        <div className="nt-filing__row" aria-label="Linked to">
          <span className="nt-muted">Linked to</span>
          {entities.map((e) => (
            <EntityChip key={e.entity_id} entity={e} />
          ))}
        </div>
      ) : null}
      {summary && item.state === "processed" ? <p className="nt-muted">{capitalize(summary)}</p> : null}
      {line ? (
        <p className={item.state === "failed" ? "nt-filing__error" : "nt-filing__state"} role={item.state === "failed" ? "alert" : "status"}>
          {item.state === "queued" ? <Icon name="clock" size={14} /> : null}
          <span>{line}</span>
          {item.state === "failed" && !item.noteId ? (
            <button type="button" className="nt-btn nt-btn--quiet" onClick={onRetry}>
              Retry
            </button>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}
