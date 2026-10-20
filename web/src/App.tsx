import { useCallback, useEffect, useMemo, useState } from "react";
import { AfAppearanceDialog, AfTopBarActions, Icon, aboutVersionsFromGateway, randomId, useAppearanceSettings, useGatewayConnection, type AfAboutVersions, type AppIdentity, type GatewayAboutPayload } from "@abstractframework/ui-kit";
import { NOT_INSTALLED, createApi, noteMount, type Mount } from "./lib/api";
import { useNarrow, useRead } from "./lib/hooks";
import { captureSessionId } from "./lib/local";
import { navigate, useRoute, VIEWS, type View } from "./lib/nav";
import type { Api } from "./lib/api";
import type { NoteSettings, NoteStatus } from "./lib/types";
import { AppContext, Spinner } from "./components/common";
import { ConnectDialog } from "./components/ConnectDialog";
import { Dialogs } from "./components/Dialogs";
import { ViewBoundary } from "./components/ViewBoundary";
import { CaptureView } from "./views/Capture";
import { GraphView } from "./views/Graph";
import { RememberView } from "./views/Remember";
import { ProcessingView } from "./views/Processing";
import { SettingsDrawer, needsRepair } from "./views/Settings";
import pkg from "../package.json";

/** The deck's vocabulary: Capture, Remember (recall), Explore (the graph). The routes keep their names. */
const LABELS: Record<View, string> = { capture: "Capture", graph: "Explore", remember: "Remember", processing: "Processing" };
const ICONS: Record<View, "compose" | "branch" | "history" | "activity"> = { capture: "compose", graph: "branch", remember: "history", processing: "activity" };

declare global {
  interface Window {
    __ABSTRACT_UI_CONFIG__?: { gateway_url?: string; base_path?: string };
  }
}

/**
 * About AbstractNote. The kit's identity descriptor (ui-kit 0.8.7) does not list abstractnote yet, so
 * `appIdentity("abstractnote")` throws; these facts come from this package's own package.json (name, version,
 * repository) and the framework website, never invented (W9 report, seam: add abstractnote to the kit descriptor).
 */
export function noteIdentity(): AppIdentity {
  const repo = String(pkg.repository?.url || "").replace(/^git\+/, "").replace(/\.git$/, "");
  if (!repo) throw new Error("package.json has no repository url");
  return { id: "abstractnote", name: "AbstractNote", version: pkg.version, website: "https://abstractframework.ai", repo, docs: `${repo}/tree/main/docs`, issues: `${repo}/issues`, feedback: `${repo}/issues/new?labels=feedback` };
}

/** The app server's own gateway address (bin/server.js injects `--gateway-url` into the page). */
export function serverUrl(): string | null {
  return (typeof window !== "undefined" && window.__ABSTRACT_UI_CONFIG__?.gateway_url) || null;
}

export function App() {
  // The framework's shared sign-in (ui-kit): gateway address, account, token, remember; the token becomes an
  // HTTP-only session in the app server and the page never keeps it. Blocking: the app has nothing to show
  // signed out.
  const conn = useGatewayConnection({ appName: "AbstractNote", variant: "blocking", defaultGatewayUrl: serverUrl() || undefined });
  const [appearance, setAppearance] = useAppearanceSettings("abstractnote", { defaults: { theme: "dark" } });
  const [appearanceOpen, setAppearanceOpen] = useState(false);
  const user = conn.status?.gateway?.principal?.user_id || null;
  return (
    <>
      {conn.phase === "connected" ? (
        <Shell key={user || "signed-in"} conn={conn} user={user} onAppearance={() => setAppearanceOpen(true)} />
      ) : (
        <div className="nt-app">
          <Header conn={conn} user={null} onAppearance={() => setAppearanceOpen(true)} tabs={null} />
          <main className="nt-main">
            <div className="nt-center">{conn.phase === "loading" ? <Spinner label="Connecting…" /> : <p className="nt-muted">Sign in to open your notebook.</p>}</div>
          </main>
        </div>
      )}
      <ConnectDialog {...conn.modalProps} serverUrl={serverUrl()} />
      <AfAppearanceDialog open={appearanceOpen} onClose={() => setAppearanceOpen(false)} value={appearance} onChange={setAppearance} />
    </>
  );
}

type Conn = ReturnType<typeof useGatewayConnection>;

/** The header: brand, the four views, and the framework's top-right cluster (appearance, about, account, sign-in pill). */
function Header({ conn, user, onAppearance, onSettings, tabs }: { conn: Conn; user: string | null; onAppearance: () => void; onSettings?: () => void; tabs: React.ReactNode }) {
  const [versions, setVersions] = useState<AfAboutVersions | undefined>(undefined);
  const identity = useMemo(noteIdentity, []);
  const refreshVersions = useCallback(() => {
    fetch("api/gateway/about", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(async (r) => (r.ok ? setVersions(aboutVersionsFromGateway((await r.json()) as GatewayAboutPayload)) : setVersions(aboutVersionsFromGateway(null, `unavailable (HTTP ${r.status})`))))
      .catch(() => setVersions(aboutVersionsFromGateway(null, "not reachable")));
  }, []);
  return (
    <header className="nt-header">
      <div className="nt-brand">
        <img src="icon.svg" alt="" width={20} height={20} />
        <span>AbstractNote</span>
      </div>
      {tabs}
      <div className="nt-header__actions">
        <AfTopBarActions
          appearance={{ onOpen: onAppearance }}
          about={{ identity, versions, onOpen: refreshVersions }}
          extraActions={
            <>
              {onSettings ? (
                <button type="button" className="af-topbar__btn" aria-label="Settings" data-af-tip="Settings" onClick={onSettings}>
                  <Icon name="gear" size={16} />
                </button>
              ) : null}
              {user ? (
                <span className="nt-account" title={`Signed in as ${user}`}>
                  <Icon name="user" size={14} />
                  <span>{user}</span>
                </span>
              ) : null}
            </>
          }
          connection={{ phase: conn.phase, signingOut: conn.signingOut, onConnect: () => conn.openModal(), onDisconnect: () => void conn.signOut() }}
        />
      </div>
    </header>
  );
}

/** Create the processor automations when the notebook has none, or repair them when the server reports one
 * unhealthy (after an upgrade; idempotent on the server; a failure is retried on the next load). */
export async function ensureProcessor(api: Api): Promise<boolean> {
  try {
    const status = await api.get<NoteStatus>("note/status");
    if (status.automations?.processor_id && status.automations?.reflect_id && !needsRepair(status.automations)) return false;
    await api.post("note/processor/ensure");
    return true;
  } catch {
    return false;
  }
}

function Shell({ conn, user, onAppearance }: { conn: Conn; user: string | null; onAppearance: () => void }) {
  const onUnauthorized = useCallback(() => void conn.refresh(), [conn]);
  const api = useMemo(() => createApi({ onUnauthorized }), [onUnauthorized]);
  const sessionId = useMemo(() => captureSessionId(randomId), []);
  const route = useRoute();
  const narrow = useNarrow(640);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const ctx = useMemo(() => ({ api, sessionId }), [api, sessionId]);

  useEffect(() => {
    document.title = `${LABELS[route.view]} · AbstractNote`;
  }, [route.view]);

  // Is AbstractNote on this gateway? Decided once: signed out opens the sign-in dialog (never "not
  // installed"); a 404 while signed in is the one sentence instead of every view saying "not in your notebook".
  const [mount, setMount] = useState<Mount | null>(null);
  useEffect(() => {
    let live = true;
    void noteMount(api).then((m) => {
      if (!live) return;
      setMount(m);
      if (m === "signed_out") {
        void conn.refresh();
        conn.openModal();
        return;
      }
      // On first use the app creates the notebook's processor and reflect automations (docs/cognition.md,
      // "The processor automation"): without them queued notes are never processed.
      if (m !== "missing") void ensureProcessor(api);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);
  const ready = mount === "mounted" || mount === "unknown";
  const settings = useRead<NoteSettings | null>(() => (ready ? api.get<NoteSettings>("note/settings") : Promise.resolve(null)), [api, ready]);

  const tabs = (
    <nav className={narrow ? "nt-tabbar" : "nt-tabs"} aria-label="Sections">
      {VIEWS.map((v) => (
        <button key={v} type="button" className="nt-tab" aria-current={route.view === v ? "page" : undefined} onClick={() => navigate(v)}>
          <Icon name={ICONS[v]} size={narrow ? 20 : 16} />
          <span>{LABELS[v]}</span>
        </button>
      ))}
    </nav>
  );

  const saveSettings = useCallback(
    async (patch: Partial<NoteSettings>) => {
      const next = await api.put<NoteSettings>("note/settings", { ...(settings.data || {}), ...patch });
      settings.setData(next);
    },
    [api, settings],
  );

  return (
    <AppContext.Provider value={ctx}>
      <div className={`nt-app${narrow ? " nt-app--narrow" : ""}`}>
        <Header conn={conn} user={user} onAppearance={onAppearance} onSettings={() => setSettingsOpen(true)} tabs={narrow ? null : tabs} />
        <main className="nt-main">
          {mount === "missing" ? (
            <div className="nt-center">
              <p className="nt-notinstalled" role="alert">
                {NOT_INSTALLED}
              </p>
            </div>
          ) : !ready ? (
            <div className="nt-center">{mount === "signed_out" ? <p className="nt-muted">Sign in to open your notebook.</p> : <Spinner label="Opening your notebook…" />}</div>
          ) : (
            <ViewBoundary key={route.view} name={route.view}>
              {route.view === "capture" ? <CaptureView route={route} processInline={Boolean(settings.data?.process_inline_default)} /> : null}
              {route.view === "graph" ? <GraphView route={route} /> : null}
              {route.view === "remember" ? <RememberView route={route} /> : null}
              {route.view === "processing" ? <ProcessingView settings={settings} onSave={saveSettings} /> : null}
            </ViewBoundary>
          )}
        </main>
        {narrow ? tabs : null}
        <SettingsDrawer notInstalled={mount === "missing"} open={settingsOpen} onClose={() => setSettingsOpen(false)} settings={settings} onSave={saveSettings} onConnection={() => conn.openModal()} />
        <ViewBoundary name="dialog">
          <Dialogs />
        </ViewBoundary>
      </div>
    </AppContext.Provider>
  );
}
