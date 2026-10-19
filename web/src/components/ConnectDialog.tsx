import { useEffect, useState } from "react";
import { GatewaySessionSignInCard, gatewayStatusBadge, normalizeGatewayUrl, type GatewayConnectionState } from "@abstractframework/ui-kit";
import { SIGNIN_KEY, initialSignIn, readRemembered, signInSentence } from "../lib/signin";
import { readLocal, writeLocal } from "../lib/local";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  blocking: boolean;
  initialStatus?: GatewayConnectionState | null;
  onStatusChange: (status: GatewayConnectionState | null) => void;
  /** The app server's own gateway address (`--gateway-url`). */
  serverUrl: string | null;
};

const CONNECTION_PATH = "api/connection/gateway";

/**
 * The framework's sign-in card (ui-kit `GatewaySessionSignInCard`, its words and look) in the kit's connect
 * overlay, with AbstractNote's defaults: the server's own address, the remembered account, one sentence when
 * sign-in is refused. The token goes to the app server, which keeps an HTTP-only session ("Keep this browser
 * signed in" keeps it across reloads); the page never stores it.
 */
export function ConnectDialog(props: Props) {
  const status = props.initialStatus ?? null;
  const start = initialSignIn(readRemembered(readLocal(SIGNIN_KEY)), props.serverUrl, status?.gateway_url);
  const [gatewayUrl, setGatewayUrl] = useState(start.gatewayUrl);
  const [user, setUser] = useState(start.user);
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [persist, setPersist] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!props.isOpen) return;
    const s = initialSignIn(readRemembered(readLocal(SIGNIN_KEY)), props.serverUrl, status?.gateway_url);
    setGatewayUrl((cur) => cur || s.gatewayUrl);
    setUser((cur) => cur || s.user);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.isOpen, props.serverUrl, status?.gateway_url]);
  if (!props.isOpen) return null;
  const badge = gatewayStatusBadge(status);
  const submit = async () => {
    setBusy(true);
    setError("");
    const url = normalizeGatewayUrl(gatewayUrl);
    try {
      const res = await fetch(CONNECTION_PATH, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ gateway_url: url, gateway_user_id: user.trim(), gateway_token: token.trim(), persist }) });
      const body = (await res.json().catch(() => null)) as GatewayConnectionState | null;
      if (!res.ok || !body || body.ok === false) {
        setError(signInSentence(res.ok ? 401 : res.status, url, props.serverUrl || status?.gateway_url || ""));
        return;
      }
      writeLocal(SIGNIN_KEY, JSON.stringify({ gateway_url: url, user: user.trim(), server_url: props.serverUrl || status?.gateway_url || url }));
      setToken("");
      props.onStatusChange(body);
      props.onClose();
    } catch {
      setError(signInSentence(0, url, props.serverUrl || ""));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="af-connect-overlay" role="presentation" onMouseDown={(e) => !props.blocking && e.target === e.currentTarget && props.onClose()}>
      <div className="af-connect-modal" role="dialog" aria-modal="true" aria-label="Sign in to your notebook">
        <GatewaySessionSignInCard
          kicker="AbstractNote connection"
          statusLabel={badge.label}
          statusTone={badge.tone}
          showGatewayUrl
          gatewayUrl={gatewayUrl}
          onGatewayUrlChange={setGatewayUrl}
          userId={user}
          userPlaceholder="your account name"
          onUserIdChange={setUser}
          token={token}
          onTokenChange={setToken}
          showToken={showToken}
          onShowTokenChange={setShowToken}
          remember={persist}
          rememberLabel="Keep this browser signed in"
          onRememberChange={setPersist}
          submitting={busy}
          showClose={!props.blocking}
          onClose={props.onClose}
          error={error}
          onSubmit={submit}
        />
      </div>
    </div>
  );
}
