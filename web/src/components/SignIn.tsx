import { useState } from "react";
import { AfSwitch } from "@abstractframework/ui-kit";

/** Sign in with an account name and its access token (the app server keeps the session; the page keeps nothing). */
export function SignIn({ onSignIn, busy }: { onSignIn: (user: string, token: string, persist: boolean) => Promise<void>; busy: boolean }) {
  const [user, setUser] = useState("");
  const [token, setToken] = useState("");
  const [persist, setPersist] = useState(true);
  const [error, setError] = useState("");
  return (
    <div className="nt-center">
      <form
        className="nt-signin"
        onSubmit={async (e) => {
          e.preventDefault();
          setError("");
          try {
            await onSignIn(user.trim(), token.trim(), persist);
          } catch {
            setError("That account name and token were not accepted.");
          }
        }}
      >
        <div className="nt-brand nt-brand--large">
          <img src="icon.svg" alt="" width={36} height={36} />
          <span>AbstractNote</span>
        </div>
        <p className="nt-muted">Sign in to your notebook.</p>
        <label className="nt-field">
          <span className="nt-field__label">Account</span>
          <input className="nt-input" autoComplete="username" value={user} onChange={(e) => setUser(e.target.value)} placeholder="your account name" />
        </label>
        <label className="nt-field">
          <span className="nt-field__label">Access token</span>
          <input className="nt-input" type="password" autoComplete="current-password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="paste your token" required />
        </label>
        <AfSwitch label="Keep me signed in" checked={persist} onChange={setPersist} variant="row" />
        {error ? (
          <p className="nt-error-text" role="alert">
            {error}
          </p>
        ) : null}
        <div className="nt-actions">
          <button type="submit" className="nt-btn nt-btn--primary" disabled={busy || !token.trim()}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </form>
    </div>
  );
}
