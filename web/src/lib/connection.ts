/**
 * Sign-in state through the app server's connection endpoint
 * (`api/connection/gateway`, relative): the server exchanges the account
 * token for an HTTP-only browser session; the page never keeps the token.
 */

import { useCallback, useEffect, useState } from "react";
import { fetchGatewayConnection, signInGateway, signOutGateway, type GatewayConnectionState } from "@abstractframework/ui-kit";
import { READ_BACKOFF_MS } from "./api";

export type Phase = "loading" | "connected" | "signed_out" | "unreachable";

export function phaseOf(status: GatewayConnectionState | null): Phase {
  if (!status) return "loading";
  return status.ok ? "connected" : "signed_out";
}

export function useConnection() {
  const [status, setStatus] = useState<GatewayConnectionState | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    for (let attempt = 0; attempt <= READ_BACKOFF_MS.length; attempt++) {
      try {
        const s = await fetchGatewayConnection();
        setStatus(s);
        setPhase(phaseOf(s));
        return;
      } catch {
        if (attempt < READ_BACKOFF_MS.length) await new Promise((r) => setTimeout(r, READ_BACKOFF_MS[attempt]));
      }
    }
    setPhase("unreachable");
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signIn = useCallback(
    async (user: string, token: string, persist: boolean) => {
      setBusy(true);
      try {
        const s = await signInGateway({ gateway_user_id: user, gateway_token: token, persist });
        setStatus(s);
        setPhase(phaseOf(s));
        if (!s.ok) throw new Error("refused");
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const signOut = useCallback(async () => {
    setBusy(true);
    try {
      await signOutGateway();
    } finally {
      setBusy(false);
      await refresh();
    }
  }, [refresh]);

  const user = status?.gateway?.principal?.user_id || null;
  return { status, phase, busy, user, refresh, signIn, signOut };
}
