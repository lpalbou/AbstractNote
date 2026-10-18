/**
 * The seeded notebook the mock gateway serves (npm run dev:mock, the
 * screenshots, the Playwright smoke). Deterministic: fixed ids and dates, so a
 * screenshot taken today and next week shows the same notebook.
 *
 * Shapes follow docs/api.md and docs/data-model.md; where api.md does not spell
 * a field out, the choice is recorded in untracked/reports/W3.md ("Seams").
 */

import { createHash } from "node:crypto";

const B32 = "abcdefghijklmnopqrstuvwxyz234567";
function b32(buf, n) {
  let bits = 0;
  let value = 0;
  let out = "";
  for (const byte of buf) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5 && out.length < n) {
      out += B32[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  return out;
}
export function makeId(prefix, key) {
  return `${prefix}_${b32(createHash("sha256").update(String(key)).digest(), 26)}`;
}

const E = {
  dana: ["ex:person-dana", "person", "Dana", ["Dana R."], "Leads the LUT prefill project; moved from the Lyon office to Paris in September."],
  marc: ["ex:person-marc", "person", "Marc", [], "Leads the watch app and, since October, the release train. Works from Lyon."],
  priya: ["ex:person-priya", "person", "Priya", [], "Benchmark engineer on LUT; knows the MLX kernels."],
  sophie: ["ex:person-sophie", "person", "Sophie", [], "Design reviewer for the note app; moving to the Castor design team."],
  lut: ["ex:project-lut", "project", "LUT", ["LUT prefill", "LUT project"], "Prefill acceleration project on the M5 Max; stopped on 2 October after three NO-GO phases."],
  castor: ["ex:project-castor", "project", "Castor", [], "The persistent entity; now runs on Qwen 27B."],
  watch: ["ex:project-watch-app", "project", "Watch app", ["the watch"], "Two-tap note capture on the Apple Watch, led by Marc."],
  pareto: ["ex:concept-pareto-analysis", "concept", "Pareto analysis", ["the Pareto"], "The analysis of LUT phases Dana asked for before funding more of them."],
  release: ["ex:event-release-0-11", "event", "Release 0.11", ["0.11"], "Shipped on 15 October with the new installers."],
  note: ["ex:software-abstractnote", "software", "AbstractNote", ["the note app"], "A notebook that keeps every note and remembers how understanding changed."],
  mlx: ["ex:software-mlx", "software", "MLX", [], "Apple's array framework, used for local models and the prefill kernels."],
  paris: ["ex:place-paris", "place", "Paris", [], "Where Dana works since September."],
  lyon: ["ex:place-lyon", "place", "Lyon", ["the Lyon office"], "The office Marc works from."],
  report: ["ex:artifact-prefill-report", "artifact", "Prefill report", [], "Priya's benchmark report on the three prefill phases."],
  effort: ["ex:concept-recall-effort", "concept", "Recall effort", ["effort dial"], "Quick, standard and deep recall."],
};

/*
 * Notes: [when (UTC), kind, title, text, topics, entity keys, claims, extra]
 * claims: [ref, subject, predicate, object (entity key or literal), {supersedes?, certainty?}]
 * extra: {tasks_open, tasks_close, status, error}
 */
const N = [
  ["2026-07-21T09:10", "idea", "A notebook that remembers how I changed my mind", "Idea: a notebook that keeps every note and remembers how my understanding of a person or a project changed over time. Call it AbstractNote.", ["abstractnote"], ["note"], []],
  ["2026-07-23T14:30", "event", "Kickoff with Dana on LUT", "Met Dana in Lyon about the LUT prefill project. She leads it from the Lyon office; the budget is 40k for the quarter and we are still exploring.", ["lut"], ["dana", "lut", "lyon"], [
    ["dana-lyon", "dana", "schema:workLocation", "lyon"],
    ["lut-budget-40", "lut", "ex:budget", "40k for the quarter"],
    ["lut-led-dana", "lut", "ex:led_by", "dana"],
    ["lut-exploring", "lut", "ex:status", "exploring"],
  ]],
  ["2026-07-24T10:00", "task", "Ask Priya for prefill numbers", "Ask Priya for the prefill benchmark numbers on the M5 Max before Friday.", ["lut", "benchmark"], ["priya", "lut"], [
    ["priya-lut", "priya", "schema:knowsAbout", "lut"],
  ], { tasks_open: [["t-priya", "Ask Priya for the prefill benchmark numbers"]] }],
  ["2026-07-28T18:20", "reflection", "Castor runs on Qwen 9B", "Castor runs on Qwen 9B for now; recall feels thin with six memories, maybe the ceiling is the problem.", ["castor"], ["castor"], [
    ["castor-9b", "castor", "ex:uses_model", "Qwen 9B"],
  ]],
  ["2026-07-30T11:00", "fact", "First prefill benchmark", "Priya shared the first prefill benchmark: the MLX baseline is 1,900 tokens/s on the M5 Max.", ["lut", "benchmark"], ["priya", "mlx", "lut", "report"], [
    ["report-priya", "report", "dcterms:creator", "priya"],
    ["lut-mlx", "lut", "ex:uses", "mlx"],
    ["lut-baseline", "lut", "ex:baseline_prefill", "1,900 tokens/s"],
  ], { tasks_close: ["t-priya"] }],
  ["2026-08-04T09:30", "decision", "Pareto before more phases", "Decided with Dana: we do a Pareto analysis of the LUT phases before funding more of them.", ["lut"], ["dana", "lut", "pareto"], [
    ["lut-pareto", "lut", "ex:requires", "pareto"],
    ["dana-pareto", "dana", "schema:knowsAbout", "pareto"],
  ]],
  ["2026-08-06T16:00", "task", "Pareto by Friday", "Dana wants the Pareto by Friday.", ["lut"], ["dana", "pareto"], [], { tasks_open: [["t-pareto", "Send Dana the Pareto analysis"]] }],
  ["2026-08-11T08:45", "idea", "Capture from the watch", "Marc thinks the watch could capture a note in two taps: dictate, send, done.", ["watch", "abstractnote"], ["marc", "watch", "note"], [
    ["watch-marc", "watch", "ex:led_by", "marc"],
    ["watch-note", "watch", "dcterms:isPartOf", "note"],
  ]],
  ["2026-08-14T13:10", "conversation", "Priya on MLX kernels", "Long chat with Priya about MLX kernels; she thinks W8A8 could reach three times the prefill speed.", ["lut", "mlx"], ["priya", "mlx", "lut"], [
    ["priya-mlx", "priya", "schema:knowsAbout", "mlx"],
    ["priya-w8a8", "priya", "ex:expects", "W8A8 could reach 3x prefill", { certainty: "probable" }],
  ]],
  ["2026-08-19T10:20", "task", "Still owe the Pareto", "Still owe Dana the Pareto.", ["lut"], ["dana", "pareto"], []],
  ["2026-08-25T17:40", "reflection", "The six-memory ceiling", "Castor's six-memory ceiling is a retrieval cap, not the model; fixing the cap made recall much richer.", ["castor"], ["castor"], [
    ["castor-cap", "castor", "ex:recall_limit", "six memories (fixed)"],
  ]],
  ["2026-09-02T09:05", "fact", "Dana moved to Paris", "Dana moved to the Paris office this week; Lyon is now only Marc.", ["people"], ["dana", "paris", "lyon", "marc"], [
    ["dana-paris", "dana", "schema:workLocation", "paris", { supersedes: "dana-lyon" }],
    ["marc-lyon", "marc", "schema:workLocation", "lyon"],
  ]],
  ["2026-09-05T15:00", "event", "Design review with Sophie", "Design review with Sophie on the note app: she wants the answer to say what was saved and how it was filed.", ["abstractnote", "design"], ["sophie", "note"], [
    ["sophie-note", "sophie", "ex:reviewed", "note"],
  ]],
  ["2026-09-09T11:30", "task", "The Pareto, again", "The Pareto for Dana, again. Third time I write this down.", ["lut"], ["dana", "pareto"], []],
  ["2026-09-14T10:00", "decision", "LUT budget cut to 25k", "Budget review: LUT goes down to 25k; Dana agrees, the rest moves to the watch app.", ["lut", "budget"], ["dana", "lut", "watch"], [
    ["lut-budget-25", "lut", "ex:budget", "25k for the quarter", { supersedes: "lut-budget-40" }],
    ["watch-budget", "watch", "ex:budget", "15k moved from LUT"],
  ]],
  ["2026-09-16T19:10", "idea", "Open on what changed", "The notebook should open on what changed since I last looked, not on a search box.", ["abstractnote", "design"], ["note"], []],
  ["2026-09-18T09:00", "fact", "Castor moved to Qwen 27B", "Moved Castor to Qwen 27B; the 9B could not hold the conversation.", ["castor"], ["castor"], [
    ["castor-27b", "castor", "ex:uses_model", "Qwen 27B", { supersedes: "castor-9b" }],
  ]],
  ["2026-09-22T14:00", "log", "MLX cache plateau", "Log: the MLX cache plateau came from decorated user turns; fixed in the runtime.", ["mlx"], ["mlx"], []],
  ["2026-09-24T08:30", "personal", "Run in the Tuileries", "Morning run in the Tuileries before the release meeting.", ["personal"], ["paris"], []],
  ["2026-09-26T16:45", "fact", "Prefill phase 1 is a NO-GO", "Prefill phase 1 is a NO-GO on the M5 Max; the report is in Priya's folder.", ["lut", "benchmark"], ["lut", "report", "priya"], [
    ["lut-phase1", "lut", "ex:phase_1_result", "NO-GO"],
    ["report-lut", "report", "dcterms:references", "lut"],
  ]],
  ["2026-09-30T10:15", "question", "Is Cider worth one more week?", "Is the Cider W8A8 path worth one more week? It is fast but fails the quality gate.", ["lut"], ["lut", "mlx"], []],
  ["2026-10-02T11:00", "decision", "LUT stopped", "All three prefill phases are NO-GO; we stop the LUT project and keep the report.", ["lut"], ["lut", "dana", "report"], [
    ["lut-stopped", "lut", "ex:status", "stopped", { supersedes: "lut-exploring" }],
  ]],
  ["2026-10-05T09:40", "event", "Marc demoed the watch capture", "Marc demoed the watch app capture: two taps, and the answer says what was saved.", ["watch"], ["marc", "watch"], []],
  ["2026-10-08T13:20", "idea", "Cue cards", "Cue cards: a few prompts the notebook asks me, like a person who went quiet.", ["abstractnote", "design"], ["note", "sophie"], []],
  ["2026-10-10T18:00", "reflection", "Dana and the budget", "Dana took the budget cut well; she wants the watch to ship before the end of the year.", ["people", "watch"], ["dana", "watch"], [
    ["dana-watch", "dana", "schema:knowsAbout", "watch"],
  ]],
  ["2026-10-13T10:30", "task", "Release checklist", "Release checklist for 0.11: pins, installers, changelog.", ["release"], ["release"], [], { tasks_open: [["t-release", "Finish the 0.11 release checklist"]] }],
  ["2026-10-15T17:00", "event", "Release 0.11 shipped", "Release 0.11 shipped with the new installers; the checklist is done.", ["release"], ["release", "note"], [
    ["release-date", "release", "schema:startDate", "2026-10-15"],
  ], { tasks_close: ["t-release"] }],
  ["2026-10-16T09:15", "fact", "Marc leads the release train", "Marc now leads the watch app and the release train.", ["release", "watch"], ["marc", "release", "watch"], [
    ["release-marc", "release", "ex:led_by", "marc"],
  ]],
  ["2026-10-17T12:00", "idea", "An effort dial for recall", "Recall needs an effort dial: quick, standard, deep.", ["abstractnote"], ["effort", "note"], [
    ["note-effort", "note", "ex:has_feature", "effort"],
  ]],
  ["2026-10-18T08:50", "task", "Call Dana", "Call Dana about the watch timeline.", ["watch"], ["dana", "watch"], [], { tasks_open: [["t-call-dana", "Call Dana about the watch timeline"]] }],
  ["2026-10-18T09:30", "reference", "Bitemporal modelling", "Read: bitemporal modelling, valid time versus system time.", ["abstractnote"], ["note"], []],
  ["2026-10-18T20:15", "personal", "Dinner with Sophie", "Dinner with Sophie in Paris; she is moving to the design team at Castor.", ["personal"], ["sophie", "paris", "castor"], [
    ["sophie-castor", "sophie", "ex:works_on", "castor"],
  ]],
  ["2026-10-18T21:40", "idea", "Ask Priya about the kernels", "Priya has been quiet since September; ask how the kernels went.", ["lut"], [], [], { status: "queued" }],
  ["2026-10-17T21:00", "log", "Release call transcript", "Transcript of the 0.11 release call (three hours).", ["release"], [], [], { status: "failed", error: "The filing model did not answer in time." }],
];

const MIN = 60_000;
const iso = (s) => (s.endsWith("Z") ? s : `${s}:00Z`);
const plus = (isoString, minutes) => new Date(Date.parse(isoString) + minutes * MIN).toISOString().replace(".000Z", "Z");

/** A fresh in-memory notebook (the mock mutates it; every server start gets its own). */
export function buildSeed() {
  const entities = new Map();
  for (const [key, [id, kind, name, aliases, summary]] of Object.entries(E)) {
    entities.set(id, {
      key, entity_id: id, kind, name, aliases: [...aliases], summary, summary_history: [],
      first_seen: null, last_seen: null, note_count: 0, merged_into: null, created_revision: null, created_at: null,
    });
  }
  const eid = (key) => E[key][0];
  const notes = new Map();
  const claims = new Map();
  const claimByRef = new Map();
  const revisions = [];
  const tasks = new Map();
  const queue = new Map();

  N.forEach(([when, kind, title, text, topics, ents, cl, extra = {}], i) => {
    const occurred = iso(when);
    const note_id = makeId("nt", `seed-${i}`);
    const status = extra.status || "processed";
    const processedAt = status === "processed" ? plus(occurred, 3) : null;
    const note = {
      note_id, kind, title, text, summary: text, occurred_at: occurred, received_at: plus(occurred, 0),
      processed_at: processedAt, status, topics: topics.map((t) => `#${t}`), priority: 1,
      source: { client: i % 3 === 0 ? "watch" : "web" }, location: ents.includes("paris") ? { lat: 48.8566, lon: 2.3522, label: "Paris" } : null,
      attachments: [], entity_ids: ents.map(eid), claim_ids: [], revision_id: null, error: extra.error || null,
    };
    if (i === 19) {
      note.attachments.push({ attachment_id: makeId("at", "report"), filename: "prefill-phase1.pdf", mime: "application/pdf", size: 482_113, extracted_by: "pdf", extracted_chars: 18_204 });
    }
    notes.set(note_id, note);
    if (status !== "processed") {
      queue.set(note_id, { note_id, priority: 1, attempts: status === "failed" ? 3 : 0, last_error: extra.error || null, enqueued_at: note.received_at });
      return;
    }
    const revision_id = makeId("rv", `${note_id}:1`);
    note.revision_id = revision_id;
    const ops = [];
    for (const k of ents) {
      const e = entities.get(eid(k));
      if (!e.created_revision) {
        e.created_revision = revision_id;
        e.created_at = occurred;
        ops.push({ op: "entity.create", entity_id: e.entity_id });
      }
      e.first_seen = e.first_seen || occurred;
      e.last_seen = occurred;
      e.note_count += 1;
    }
    const superseded = [];
    for (const [ref, s, p, o, opt = {}] of cl) {
      const isEntity = Boolean(E[o]);
      const claim_id = makeId("cl", `${note_id}:${ref}`);
      const claim = {
        claim_id, subject_id: eid(s), predicate: p,
        object_id: isEntity ? eid(o) : null, object_literal: isEntity ? null : o,
        certainty: opt.certainty || "stated", stance: opt.supersedes ? "revise" : "assert",
        observed_at: occurred, valid_from: occurred, valid_until: null, status: "active",
        superseded_by: null, supersedes: null, evidence: text, note_id, revision_id, recorded_at: plus(occurred, 3),
      };
      if (opt.supersedes) {
        const old = claimByRef.get(opt.supersedes);
        old.valid_until = occurred;
        old.status = "superseded";
        old.superseded_by = claim_id;
        claim.supersedes = old.claim_id;
        superseded.push(old.claim_id);
        ops.push({ op: "claim.supersede", claim_id: old.claim_id, by_claim_id: claim_id });
      }
      claims.set(claim_id, claim);
      claimByRef.set(ref, claim);
      note.claim_ids.push(claim_id);
      ops.push({ op: "claim.add", claim_id });
    }
    for (const [tid, ttext] of extra.tasks_open || []) {
      const task_id = makeId("tk", tid);
      tasks.set(task_id, { task_id, text: ttext, status: "open", note_id, opened_at: occurred, closed_at: null, closed_by_note_id: null, entity_ids: ents.map(eid), mentions: 1 });
      ops.push({ op: "task.open", task_id });
    }
    for (const tid of extra.tasks_close || []) {
      const t = tasks.get(makeId("tk", tid));
      t.status = "done";
      t.closed_at = occurred;
      t.closed_by_note_id = note_id;
      ops.push({ op: "task.close", task_id: t.task_id });
    }
    const names = ents.map((k) => E[k][2]);
    const summary = superseded.length
      ? `Filed under ${note.topics.join(", ")}; updated what you knew about ${names.slice(0, 2).join(" and ")}.`
      : names.length
        ? `Linked to ${names.slice(0, 3).join(", ")}.`
        : "Filed with no new links.";
    revisions.push({ revision_id, note_id, at: occurred, recorded_at: plus(occurred, 3), summary, ops, source: { client: note.source.client } });
  });

  // The Pareto task: mentioned three times, never closed.
  const pareto = tasks.get(makeId("tk", "t-pareto"));
  pareto.mentions = 3;

  // Summary history for the entities whose understanding changed.
  const hist = (key, rows) => {
    entities.get(eid(key)).summary_history = rows.map(([at, summary]) => ({ at: iso(at), summary }));
  };
  hist("dana", [["2026-07-23T14:30", "Leads the LUT prefill project from the Lyon office."], ["2026-09-02T09:05", "Leads the LUT prefill project; moved from the Lyon office to Paris in September."]]);
  hist("lut", [["2026-07-23T14:30", "Prefill acceleration project led by Dana; 40k budget for the quarter, still exploring."], ["2026-09-14T10:00", "Budget cut to 25k; the rest moved to the watch app."], ["2026-10-02T11:00", "Prefill acceleration project on the M5 Max; stopped on 2 October after three NO-GO phases."]]);
  hist("castor", [["2026-07-28T18:20", "The persistent entity; runs on Qwen 9B, recall feels thin."], ["2026-09-18T09:00", "The persistent entity; now runs on Qwen 27B."]]);

  const insights = new Map();
  const noteIdx = (i) => makeId("nt", `seed-${i}`);
  const addInsight = (key, at, insight_kind, title, body, evidence, ents) => {
    const note_id = makeId("nt", `insight-${key}`);
    insights.set(note_id, { note_id, kind: "insight", insight_kind, title, body, occurred_at: iso(at), evidence_note_ids: evidence.map(noteIdx), entity_ids: ents.map(eid), acked_at: null });
  };
  addInsight("pareto", "2026-10-12T06:00", "reminder", "You promised Dana the Pareto three times", "The Pareto analysis for Dana was written down on 6 August, 19 August and 9 September and never marked done.", [6, 9, 13], ["dana", "pareto"]);
  addInsight("lut", "2026-10-03T06:00", "evolution", "LUT went from funded to stopped in ten weeks", "LUT started on 23 July with 40k, was cut to 25k on 14 September and stopped on 2 October after three NO-GO phases.", [1, 14, 21], ["lut"]);

  const cues = [
    // Cues are insight notes; cue_id is the note id (docs/api.md pinned shapes).
    { cue_id: makeId("nt", "cue-priya"), kind: "quiet_person", text: "Priya has not come up since 26 September. What is she working on now?", subject: { kind: "entity", id: eid("priya"), title: "Priya" } },
    { cue_id: makeId("nt", "cue-pareto"), kind: "lingering_task", text: "You wrote \"the Pareto for Dana\" three times. Is it done?", subject: { kind: "task", id: pareto.task_id, title: "Send Dana the Pareto analysis" } },
    { cue_id: makeId("nt", "cue-castor"), kind: "aged_decision", text: "Castor moved to Qwen 27B a month ago. Is it still the right model?", subject: { kind: "entity", id: eid("castor"), title: "Castor" } },
  ];

  // W1 DEFAULT_SETTINGS shape.
  const settings = {
    filing_model: { provider: null, model: null },
    answering_model: { provider: null, model: null },
    reflect_schedule: { every: "7d" },
    process_inline_default: false,
  };

  return { entities, notes, claims, revisions, tasks, queue, insights, cues, settings, entityKey: eid };
}
