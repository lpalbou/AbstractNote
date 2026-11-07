/**
 * Capture's dictation rules: when the microphone is offered, and the sentence a failed transcription shows.
 *
 * The gateway says whether it can transcribe in its capability contract,
 * `capabilities.contracts.assistant.voice.stt.available` (`GET discovery/capabilities?scope=voice`; gateways
 * before 0.20.2 ignore `scope` and answer the full list, which carries the same contract). The microphone is
 * offered only when that is `true`; otherwise it is disabled with a plain reason.
 */
import { ApiError } from "./api";

/** The capability read: still asking, the gateway transcribes, it does not, or it could not be asked. */
export type DictationCapability = "checking" | "available" | "missing" | "unknown";

export const DICTATION_TEXT = {
  missing: "Your gateway has no speech-to-text set up, so dictation is unavailable.",
  unknown: "Dictation is unavailable because your gateway did not say whether it can transcribe. Reload the page to try again.",
} as const;

/** Reads the voice contract's speech-to-text flag from the scoped or the full capability answer. */
export function sttAvailable(payload: unknown): boolean {
  const p = (payload && typeof payload === "object" ? payload : {}) as Record<string, any>;
  const contracts = p.capabilities?.contracts ?? p.contracts;
  return contracts?.assistant?.voice?.stt?.available === true;
}

/**
 * Why the microphone cannot be used, or `null` when it can. `insecure` is the kit's plain-http explanation
 * (`insecureContextReason("the microphone")`), which outranks the gateway's answer: over http the browser
 * refuses the microphone whatever the gateway can do.
 */
export function dictationUnavailableReason(capability: DictationCapability, insecure: string | null): string | null {
  if (insecure) return insecure;
  if (capability === "missing") return DICTATION_TEXT.missing;
  if (capability === "unknown") return DICTATION_TEXT.unknown;
  return null;
}

/** The one sentence Capture shows when a recording could not be turned into text. The draft is never changed. */
export function dictationErrorSentence(err: unknown): string {
  const e = err instanceof ApiError ? err : null;
  if (e?.code === "timeout") {
    return "Your gateway is still transcribing after 2 minutes, so the words will not reach this draft. Try again, perhaps with a shorter recording.";
  }
  if (e?.status === 0) return "Your gateway could not be reached, so the recording was not transcribed. Try again.";
  if (e?.status === 401) return "Please sign in again.";
  if (e?.status === 403) return "You are not allowed to use dictation on this gateway.";
  if (e?.code === "too_large" || e?.status === 413) return "The recording is too long to send (over 25 MB). Try a shorter one.";
  if (e?.status === 404 || e?.status === 501) return "Your gateway cannot transcribe recordings.";
  return "Your gateway could not transcribe the recording. Try again.";
}
