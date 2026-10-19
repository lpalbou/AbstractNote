import { useCallback, useEffect, useMemo, useState } from "react";
import { AfAppearanceDialog, Icon, randomId, useAppearanceSettings } from "@abstractframework/ui-kit";
import { createApi } from "./lib/api";
import { useConnection } from "./lib/connection";
import { useNarrow, useRead } from "./lib/hooks";
import { captureSessionId } from "./lib/local";
import { navigate, useRoute, VIEWS, type View } from "./lib/nav";
import type { Api } from "./lib/api";
import type { NoteSettings, NoteStatus } from "./lib/types";
import { AppContext, Spinner } from "./components/common";
import { Dialogs } from "./components/Dialogs";
import { ViewBoundary } from "./components/ViewBoundary";
import { SignIn } from "./components/SignIn";
import { CaptureView } from "./views/Capture";
import { GraphView } from "./views/Graph";
import { RememberView } from "./views/Remember";
import { SettingsDrawer } from "./views/Settings";

const LABELS: Record<View, string> = { capture: "Capture", graph: "Graph", remember: "Remember" };
const ICONS: Record<View, "compose" | "branch" | "history"> = { capture: "compose", graph: "branch", remember: "history" };

export function App() {
  const conn = useConnection();
  const [appearance, setAppearance] = useAppearanceSettings("abstractnote", { defaults: { theme: "dark" } });
  const [appearanceOpen, setAppearanceOpen] = useState(false);

  if (conn.phase === "loading") {
    return (
      <div className="nt-center">
        <Spinner label="Connecting…" />
      </div>
    );
  }
  if (conn.phase === "unreachable") {
    return (
      <div className="nt-center">
        <div className="nt-error" role="alert">
          <span>Your notebook could not be reached.</span>
          <button type="button" className="nt-btn nt-btn--quiet" onClick={() => void conn.refresh()}>
            Retry
          </button>
        </div>
      </div>
    );
  }
  if (conn.phase === "signed_out") return <SignIn onSignIn={conn.signIn} busy={conn.busy} />;
  return (
    <>
      <Shell user={conn.user} onSignOut={conn.signOut} signingOut={conn.busy} onUnauthorized={() => void conn.refresh()} onAppearance={() => setAppearanceOpen(true)} />
      <AfAppearanceDialog open={appearanceOpen} onClose={() => setAppearanceOpen(false)} value={appearance} onChange={setAppearance} />
    </>
  );
}

/** Create the processor automations when the notebook has none (idempotent on the server; a failure is retried on the next load). */
export async function ensureProcessor(api: Api): Promise<boolean> {
  try {
    const status = await api.get<NoteStatus>("note/status");
    if (status.automations?.processor_id && status.automations?.reflect_id) return false;
    await api.post("note/processor/ensure");
    return true;
  } catch {
    return false;
  }
}

function Shell({ user, onSignOut, signingOut, onUnauthorized, onAppearance }: { user: string | null; onSignOut: () => Promise<void>; signingOut: boolean; onUnauthorized: () => void; onAppearance: () => void }) {
  const api = useMemo(() => createApi({ onUnauthorized }), [onUnauthorized]);
  const sessionId = useMemo(() => captureSessionId(randomId), []);
  const route = useRoute();
  const narrow = useNarrow(640);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settings = useRead<NoteSettings>(() => api.get<NoteSettings>("note/settings"), [api]);
  const ctx = useMemo(() => ({ api, sessionId }), [api, sessionId]);

  useEffect(() => {
    document.title = `${LABELS[route.view]} · AbstractNote`;
  }, [route.view]);

  // On first use the app creates the notebook's processor and reflect automations (docs/cognition.md,
  // "The processor automation"): without them queued notes are never processed.
  useEffect(() => {
    void ensureProcessor(api);
  }, [api]);

  const tabs = (
    <nav className={narrow ? "nt-tabbar" : "nt-tabs"} aria-label="Views">
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
        <header className="nt-header">
          <div className="nt-brand">
            <img src="icon.svg" alt="" width={22} height={22} />
            <span>AbstractNote</span>
          </div>
          {narrow ? null : tabs}
          <div className="nt-header__actions">
            <button type="button" className="nt-icon-btn" aria-label="Settings" title="Settings" onClick={() => setSettingsOpen(true)}>
              <Icon name="gear" size={18} />
            </button>
            <button type="button" className="nt-icon-btn" aria-label="Appearance" title="Appearance" onClick={onAppearance}>
              <Icon name="contrast" size={18} />
            </button>
          </div>
        </header>
        <main className="nt-main">
          <ViewBoundary key={route.view} name={route.view}>
            {route.view === "capture" ? <CaptureView route={route} processInline={Boolean(settings.data?.process_inline_default)} /> : null}
            {route.view === "graph" ? <GraphView route={route} /> : null}
            {route.view === "remember" ? <RememberView route={route} /> : null}
          </ViewBoundary>
        </main>
        {narrow ? tabs : null}
        <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} settings={settings} onSave={saveSettings} user={user} onSignOut={onSignOut} signingOut={signingOut} onAppearance={onAppearance} />
        <ViewBoundary name="dialog">
          <Dialogs />
        </ViewBoundary>
      </div>
    </AppContext.Provider>
  );
}
