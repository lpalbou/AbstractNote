/**
 * The Docs assistant (top-bar set, operator ruling 2026-10-29): panel-chat's DocsAssistantDrawer,
 * the same chat the console and every app mount, grounded on THIS app's llms.txt (served from the
 * build at /llms.txt; the gateway reads it at `docs/corpus?app=note`) through the gateway's docs-qa
 * workflow. Mounted at the app root and kept alive while closed, so a conversation survives closing
 * and the notebook re-opening.
 */
import { DocsAssistantDrawer, type DocsAssistantSource, type GatewayFetch } from "@abstractframework/panel-chat";
import { CSRF_COOKIE, CSRF_HEADER, readCookie } from "../lib/api";

export const NOTE_DOCS_SOURCE: DocsAssistantSource = { app: "note", name: "AbstractNote" };

export const NOTE_DOCS_SUGGESTIONS = ["How do I capture a note?", "What does Remember search?", "What happens to a note after I capture it?"];

/** Through this app's session proxy (relative `api/gateway/…`): the session cookie, plus the
 * proxy's CSRF header on writes (the docs-qa run start), like every other call the app makes. */
export function makeNoteDocsFetch(csrf: () => string = () => (typeof document === "undefined" ? "" : readCookie(CSRF_COOKIE, document.cookie)), doFetch: typeof fetch = (input, init) => fetch(input, init)): GatewayFetch {
  return (path, init = {}) => {
    const headers = new Headers(init.headers || {});
    const method = String(init.method || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD") headers.set(CSRF_HEADER, csrf());
    return doFetch(path, { ...init, headers, credentials: "same-origin" });
  };
}

const noteDocsFetch = makeNoteDocsFetch();

export function NoteDocsAssistant(props: { open: boolean; onClose: () => void; connected: boolean }) {
  return (
    <DocsAssistantDrawer
      open={props.open}
      onClose={props.onClose}
      source={NOTE_DOCS_SOURCE}
      fetchGateway={noteDocsFetch}
      connected={props.connected}
      topOffset="auto"
      placeholder="Ask about AbstractNote…"
      suggestions={NOTE_DOCS_SUGGESTIONS}
    />
  );
}
