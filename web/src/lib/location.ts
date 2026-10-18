/** Opt-in location for captures: asked once per browser session, cached for it. */

import { LOCATION_CACHE_KEY, LOCATION_KEY, readLocal, writeLocal } from "./local";

export type Place = { lat: number; lon: number; accuracy_m?: number };

export function locationEnabled(): boolean {
  return readLocal(LOCATION_KEY) === "on";
}
export function setLocationEnabled(on: boolean): void {
  writeLocal(LOCATION_KEY, on ? "on" : "off");
  if (!on) writeLocal(LOCATION_CACHE_KEY, null, "session");
}

/** The cached place, or one fresh ask (5 s); null when off, refused or unavailable. Never blocks a capture longer. */
export async function currentPlace(): Promise<Place | null> {
  if (!locationEnabled()) return null;
  const cached = readLocal(LOCATION_CACHE_KEY, "session");
  if (cached === "refused") return null;
  if (cached) {
    try {
      return JSON.parse(cached) as Place;
    } catch {
      // fall through to a fresh ask
    }
  }
  if (typeof navigator === "undefined" || !navigator.geolocation) return null;
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const place = { lat: Math.round(pos.coords.latitude * 1e5) / 1e5, lon: Math.round(pos.coords.longitude * 1e5) / 1e5, accuracy_m: Math.round(pos.coords.accuracy) };
        writeLocal(LOCATION_CACHE_KEY, JSON.stringify(place), "session");
        resolve(place);
      },
      () => {
        writeLocal(LOCATION_CACHE_KEY, "refused", "session");
        resolve(null);
      },
      { timeout: 5000, maximumAge: 10 * 60_000 },
    );
  });
}
