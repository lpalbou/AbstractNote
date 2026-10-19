/**
 * Copy rules (COMMON.md rule 7, abstractuic state-toggles.md): no system vocabulary in what a
 * person reads, and no verb toggles. Scans the strings the views render.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { findVerbToggleLabels } from "@abstractframework/ui-kit";
import { deltaVocabulary } from "./delta_words";
import { ACK_WORDS } from "../src/lib/ack";
import { NOT_INSTALLED } from "../src/lib/api";

const SRC = join(__dirname, "..", "src");
const FORBIDDEN = /\b(ledger|gateway|workflow|run ids?|records?|runs?|artifacts?|automations?|entity id|note id|claim id|revision id|http \d{3})\b/i;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(f) ? [p] : [];
  });
}

/** Strings a person can read: JSX text and string/template literals that contain a space or start upper-case. */
export function visibleStrings(source: string): string[] {
  const out: string[] = [];
  const code = source.replace(/^\s*(import|export \{)[^;]*;$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  for (const m of code.matchAll(/>([^<>{}]*[A-Za-z][^<>{}]*)</g)) {
    const t = m[1].trim();
    if (/[;=]|\|\||&&|\)\./.test(t)) continue; // code between comparison signs, not JSX text
    out.push(t);
  }
  for (const m of code.matchAll(/"([^"\n]*)"|`([^`\n]*)`/g)) {
    const s = (m[1] ?? m[2] ?? "").trim();
    if (!s || !/[A-Za-z]/.test(s)) continue;
    if (/^[a-z0-9_./:@-]+$/i.test(s) && !/^[A-Z]/.test(s)) continue; // paths, keys, class names
    if (/^(api\/|note\/|runs\/|discovery\/|automations\/|attachments\/)/.test(s)) continue;
    if (/^nt-|^pc-|^af-/.test(s)) continue;
    out.push(s);
  }
  return out;
}

describe("copy rules", () => {
  it("the scanner finds system words (guard against a silent scanner)", () => {
    expect(visibleStrings('<p>Open the ledger</p>').some((s) => FORBIDDEN.test(s))).toBe(true);
    expect(visibleStrings('const x = "The gateway is down";').some((s) => FORBIDDEN.test(s))).toBe(true);
    expect(visibleStrings('api.get("note/cues")').some((s) => FORBIDDEN.test(s))).toBe(false);
  });

  it("no view or component shows system vocabulary", () => {
    const hits: string[] = [];
    for (const f of files(SRC).filter((p) => /views|components|App\.tsx/.test(p))) {
      for (const s of visibleStrings(readFileSync(f, "utf8"))) if (FORBIDDEN.test(s)) hits.push(`${f.replace(SRC, "src")}: ${s}`);
    }
    expect(hits).toEqual([]);
  });

  it("every error sentence the client writes is one sentence without codes", () => {
    const src = readFileSync(join(SRC, "lib", "api.ts"), "utf8") + readFileSync(join(SRC, "lib", "capture.ts"), "utf8");
    const sentences = [...src.matchAll(/return `([^`]+)`;|return "([^"]+)";|error: "([^"]+)"/g)].map((m) => m[1] || m[2] || m[3]).filter((s) => /^[A-Z$]/.test(s));
    expect(sentences.length).toBeGreaterThan(5);
    for (const s of sentences) {
      expect(FORBIDDEN.test(s), s).toBe(false);
      expect(s.replace(/\$\{[^}]+\}/g, "x").split(/[.!?](\s|$)/).filter((x) => x && x.trim()).length, s).toBeLessThanOrEqual(1);
    }
  });

  it("a vocabulary term in the since-you-last-looked delta fails (guard)", () => {
    expect(deltaVocabulary({ superseded_claims: [{ text: "Dana schema:participant LUT" }] })).toEqual(["Dana schema:participant LUT"]);
    expect(deltaVocabulary({ superseded_claims: [{ text: "Dana participant LUT" }], entities: [{ name: "Dana" }] })).toEqual([]);
  });

  it("the delta recorded from the hermetic gateway reads in plain words", () => {
    const recorded = join(__dirname, "..", "..", "watch", "apple", "Packages", "AbstractGatewayClient", "Tests",
      "AbstractGatewayClientTests", "Fixtures", "recorded", "note_changes_200.json");
    const delta = JSON.parse(readFileSync(recorded, "utf8")).body;
    expect(delta.superseded_claims.length).toBeGreaterThan(0); // a recording without one would check nothing
    expect(deltaVocabulary(delta)).toEqual([]);
  });

  it("capture states use the deck's words, plainly, one word or two each", () => {
    const words = Object.values(ACK_WORDS);
    expect(words).toEqual(expect.arrayContaining(["Received", "Organizing", "Organized", "Needs attention"]));
    for (const w of words) {
      expect(FORBIDDEN.test(w), w).toBe(false);
      expect(w.split(" ").length, w).toBeLessThanOrEqual(2);
    }
    // The view renders the words from the table (never its own spelling of a state).
    const capture = readFileSync(join(SRC, "views", "Capture.tsx"), "utf8");
    expect(capture).toContain("ACK_WORDS[state]");
    for (const w of words) expect(capture.includes(`"${w}"`), `Capture.tsx spells "${w}" itself`).toBe(false);
  });

  it("the one sentence that names the gateway is the not-installed one (the fix is on the gateway)", () => {
    expect(FORBIDDEN.test(NOT_INSTALLED)).toBe(true); // a real exception, not a scanner blind spot
    expect(NOT_INSTALLED).toBe("AbstractNote is not installed on this gateway.");
    const app = readFileSync(join(SRC, "App.tsx"), "utf8");
    expect(app).toContain("{NOT_INSTALLED}");
  });

  it("no on/off setting is labelled with a verb", () => {
    for (const f of files(SRC)) expect(findVerbToggleLabels(readFileSync(f, "utf8")), f).toEqual([]);
  });
});
