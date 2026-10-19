/**
 * "Why this surfaced": the plain-words reason an insight or a cue is shown, from its `insight_kind`
 * (docs/data-model.md "Insight": warning, reminder, connection, contradiction, evolution, dossier, cue).
 * A fixed table from a stored field to a sentence; it never reads the insight's text.
 */

export const WHY_WORDS: Record<string, string> = {
  reminder: "Something you wrote down is still open.",
  connection: "Notes written apart turn out to be linked.",
  contradiction: "Two of your notes disagree.",
  evolution: "Your understanding of this changed over time.",
  warning: "Something in your notes may need a look.",
  dossier: "You asked for this investigation.",
  cue: "It has been quiet for a while.",
};

export function whyLine(kind: string | null | undefined): string {
  return WHY_WORDS[String(kind || "")] || "Found in the regular look back over your notes.";
}

/** "Based on 3 notes" / "Based on 1 note". */
export function basisLine(count: number): string {
  if (!count) return "";
  return count === 1 ? "Based on 1 note" : `Based on ${count} notes`;
}
