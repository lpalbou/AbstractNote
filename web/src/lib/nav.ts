/**
 * Hash navigation (works at / and under /apps/note/ alike) and the time shared
 * by Graph and Remember: `at` = the moment the views show (null = now).
 *
 *   #/capture?about=<id>&kind=<insight|task|cue|entity|note>&title=<title>
 *   #/graph?focus=<id>,<id>&depth=2&entity=<id>&day=YYYY-MM-DD
 *   #/remember
 */

import { useEffect, useState } from "react";
import { createStore, useStore } from "./hooks";

export type View = "capture" | "graph" | "remember";
/** In the order the tabs show them (deck: Capture, Recall, Explore). */
export const VIEWS: View[] = ["capture", "remember", "graph"];
export type Route = { view: View; params: URLSearchParams };

export function parseHash(hash: string): Route {
  const raw = String(hash || "").replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  const view = (VIEWS as string[]).includes(path) ? (path as View) : "capture";
  return { view, params: new URLSearchParams(query || "") };
}

export function routeHash(view: View, params: Record<string, string | number | null | undefined> = {}): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return `#/${view}${s ? `?${s}` : ""}`;
}

export function navigate(view: View, params: Record<string, string | number | null | undefined> = {}): void {
  window.location.hash = routeHash(view, params);
}

/** Open Capture to write a note linked to a subject (sent as `context.about`). */
export function noteAbout(kind: "insight" | "task" | "cue" | "entity" | "note", id: string, title: string): void {
  navigate("capture", { about: id, kind, title });
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

/** The moment Graph and Remember show; null = now. */
export const atStore = createStore<string | null>(null);
export function useAt(): [string | null, (next: string | null) => void] {
  return useStore(atStore);
}

/**
 * Explore's reading of the chosen date: false = what was true then (`at`, validity), true = what the notebook
 * held then (`as_received`, the archive snapshot: notes received by that date and what they caused).
 */
export const receivedStore = createStore<boolean>(false);
export function useByReceived(): [boolean, (next: boolean) => void] {
  return useStore(receivedStore);
}
/** The date parameters of a read for the chosen moment in Explore. */
export function momentParams(at: string | null, byReceived: boolean): Record<string, string> {
  if (!at) return {};
  return byReceived ? { as_received: at } : { at };
}
