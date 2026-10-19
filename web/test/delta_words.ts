/**
 * The "since you last looked" delta is shown as the server words it (the watch prints `text`
 * verbatim): a vocabulary term such as `schema:participant` or an id there is system vocabulary on
 * a person's screen (COMMON.md rule 7, review 02 M3). Returns every text that carries one.
 */
export const CURIE = /\b[a-z][a-z0-9]*:[A-Za-z]/;

type Delta = {
  superseded_claims?: Array<{ text?: string }>;
  closed_loops?: Array<{ text?: string }>;
  entities?: Array<{ name?: string }>;
};

export function deltaVocabulary(delta: Delta): string[] {
  const texts = [
    ...(delta.superseded_claims || []).map((c) => c.text || ""),
    ...(delta.closed_loops || []).map((t) => t.text || ""),
    ...(delta.entities || []).map((e) => e.name || ""),
  ];
  return texts.filter((t) => CURIE.test(t));
}
