// The shared top-bar set (operator ruling 2026-10-29: "change the widget once, it changes everywhere"):
// the docs drawer button, the theme picker (Appearance), the About dialog and the account widget are the
// KIT's components, never local copies. Note passes all four to the kit's AfTopBarActions: the docs toggle
// opens panel-chat's DocsAssistantDrawer (grounded on this app's llms.txt), Appearance opens the kit's
// AfAppearanceDialog, `about` renders the kit's AfAboutDialog, and identity="menu" with the
// useGatewayConnection value renders its ONE account widget (the local account span is gone). Removing a
// piece from App.tsx (or re-implementing one locally) turns this red.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AfTopBarActions } from "@abstractframework/ui-kit";
import { NOTE_DOCS_SOURCE, makeNoteDocsFetch } from "../src/components/DocsAssistant";
import { noteIdentity } from "../src/App";

const SRC = join(__dirname, "..", "src");
const app = readFileSync(join(SRC, "App.tsx"), "utf8");

/** The props text of the first `<Tag ... />` in `src` (brace-aware: `/>` inside `{...}` does not end it). */
function jsxProps(src: string, tag: string): string {
  const start = src.indexOf(`<${tag}`);
  expect(start, `<${tag} in App.tsx`).toBeGreaterThanOrEqual(0);
  let depth = 0;
  for (let i = start; i < src.length; i++) {
    const c = src[i];
    if (c === "{") depth++;
    else if (c === "}") depth--;
    else if (depth === 0 && c === "/" && src[i + 1] === ">") return src.slice(start, i);
  }
  throw new Error(`<${tag} is never closed`);
}

describe("the shared top-bar set", () => {
  const bar = jsxProps(app, "AfTopBarActions");

  it("passes the four kit pieces to AfTopBarActions", () => {
    expect(bar).toMatch(/\bdocs=\{\{ open: docs\.open, onToggle: docs\.onToggle,/);
    expect(bar).toMatch(/\bappearance=\{\{ onOpen: onAppearance \}\}/);
    expect(bar).toMatch(/\babout=\{\{ identity, versions, onOpen: refreshVersions \}\}/);
    expect(bar).toContain('identity="menu"');
    // The hook's value: the account reads conn.status, its Disconnect calls conn.signOut().
    expect(bar).toMatch(/\bconnection=\{conn\}/);
  });

  it("opens the kit dialog and the panel-chat docs drawer, never local copies", () => {
    expect(jsxProps(app, "AfAppearanceDialog")).toMatch(/open=\{appearanceOpen\}/);
    expect(jsxProps(app, "NoteDocsAssistant")).toMatch(/open=\{docsOpen\}[\s\S]*connected=\{conn\.phase === "connected"\}/);
    const drawer = readFileSync(join(SRC, "components", "DocsAssistant.tsx"), "utf8");
    expect(drawer).toMatch(/import \{ DocsAssistantDrawer[^}]*\} from "@abstractframework\/panel-chat"/);
    for (const local of [/nt-account/, /function \w*About\w*Dialog/, /function \w*Appearance\w*Dialog/, /af-topbar__identity/]) expect(app).not.toMatch(local);
    expect(readFileSync(join(SRC, "styles.css"), "utf8")).not.toMatch(/\.nt-account\b/);
  });

  it("the docs corpus: this app's llms.txt ships in the build (the gateway reads it at docs/corpus?app=note)", () => {
    expect(NOTE_DOCS_SOURCE).toEqual({ app: "note", name: "AbstractNote" });
    const vite = readFileSync(join(__dirname, "..", "vite.config.ts"), "utf8");
    expect(vite).toMatch(/const LLMS_TXT = resolve\(__dirname, "\.\.\/llms\.txt"\);/);
    expect(vite).toMatch(/this\.emitFile\(\{ type: "asset", fileName: "llms\.txt"/);
    expect(vite).toMatch(/plugins: \[[^\]]*llmsTxtPlugin\(\)/);
    expect(existsSync(join(__dirname, "..", "..", "llms.txt"))).toBe(true);
  });

  it("the docs fetch goes through the session proxy, with the CSRF header on writes only", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const doFetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init: init || {} });
      return new Response("{}");
    }) as unknown as typeof fetch;
    const f = makeNoteDocsFetch(() => "tok", doFetch);
    await f("api/gateway/docs/corpus?app=note");
    await f("api/gateway/runs/start", { method: "POST", body: "{}" });
    expect(calls.map((c) => c.url)).toEqual(["api/gateway/docs/corpus?app=note", "api/gateway/runs/start"]);
    expect(new Headers(calls[0].init.headers).get("x-abstractnote-csrf")).toBeNull();
    expect(new Headers(calls[1].init.headers).get("x-abstractnote-csrf")).toBe("tok");
    expect(calls.every((c) => c.init.credentials === "same-origin")).toBe(true);
  });

  it("the installed kit renders the set with the one account widget (ui-kit ^0.9.6)", () => {
    const noop = () => {};
    const status = { ok: true, has_session: true, gateway_url: "http://gw.local:8080", gateway: { ok: true, principal: { user_id: "alice" } } };
    const html = renderToStaticMarkup(
      createElement(AfTopBarActions, {
        docs: { open: false, onToggle: noop },
        appearance: { onOpen: noop },
        about: { identity: noteIdentity() },
        identity: "menu",
        connection: { phase: "connected", onConnect: noop, onDisconnect: noop, status },
      }),
    );
    const at = ["af-topbar__btn--docs", "af-topbar__btn--appearance", "af-topbar__btn--about", 'data-af-identity="menu"'].map((m) => html.indexOf(m));
    expect(at.every((v, i) => v >= 0 && (i === 0 || v > at[i - 1]))).toBe(true);
    expect(html).toContain('<span class="af-topbar__identity-label">alice</span>');
    expect(html).not.toContain("af-topbar__pill");
  });
});
