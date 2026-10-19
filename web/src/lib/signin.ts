/**
 * The sign-in dialog's defaults and its one sentence on failure (operator, live session 2026-10-19:
 * "can barely connect").
 *
 * - The address defaults to the app server's own gateway (`--gateway-url`, given to the page as
 *   `window.__ABSTRACT_UI_CONFIG__.gateway_url`): the server refuses any other unless it allows browser
 *   addresses, so a stale address must never be the default.
 * - The last address and account that signed in are remembered per browser; the address is reused only
 *   while the server still points where it pointed then.
 * - A refusal reads as one sentence with what to do, never a raw detail or an HTTP code.
 */

export const SIGNIN_KEY = "abstractnote.signin.v1";
export type Remembered = { gateway_url: string; user: string; server_url: string };

export function readRemembered(raw: string | null): Remembered | null {
  try {
    const v = JSON.parse(raw || "null");
    if (v && typeof v.gateway_url === "string" && typeof v.user === "string" && typeof v.server_url === "string") return v;
  } catch {
    // an older or broken value: nothing remembered
  }
  return null;
}

const trim = (u: string | null | undefined) => String(u || "").trim().replace(/\/+$/, "");

/** The address and account the dialog opens with. */
export function initialSignIn(remembered: Remembered | null, serverUrl: string | null | undefined, statusUrl?: string | null): { gatewayUrl: string; user: string } {
  const server = trim(serverUrl) || trim(statusUrl);
  const sameServer = Boolean(remembered && server && trim(remembered.server_url) === server);
  return { gatewayUrl: sameServer ? trim(remembered!.gateway_url) || server : server, user: remembered?.user || "" };
}

/** The one sentence after a refused sign-in (`status` 0 = nothing answered). */
export function signInSentence(status: number, url: string, serverUrl: string): string {
  if (status === 400 || status === 401 || status === 422) return "That account and token were not accepted; check both and sign in again.";
  if (status === 403) return `This app signs in only at ${trim(serverUrl) || "its own address"}; use that address.`;
  if (status === 0 || status === 502 || status === 503 || status === 504) return `Nothing answered at ${trim(url) || "that address"}; check the address and that it is running.`;
  return "Signing in did not work; try again.";
}
