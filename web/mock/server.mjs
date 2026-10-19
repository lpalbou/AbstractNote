/**
 * A mock AbstractGateway for the AbstractNote web app: the sign-in routes the
 * app-server session proxy calls (/session/login, /me, /session/logout), the
 * runs API pieces the app uses (attachments/upload, runs/start, runs/{id},
 * ledger and its SSE stream, automations commands), provider discovery, and
 * every route of the note read API (docs/api.md) over a seeded notebook
 * (mock/seed.mjs). The flows are faked: note-capture files the note, answers
 * through an `answer_user` ledger step and `output.response`, and leaves the
 * note queued; a fake processor picks queued notes up after a few seconds.
 *
 *   node mock/server.mjs [--port 18890] [--host 127.0.0.1] [--process-delay-ms 6000]
 *
 * Sign in with any user name and the token `demo-token`.
 * POST /__mock/fail {"path": "/api/gateway/note/cues", "count": 3, "status": 503}
 * makes the next reads of a path fail (for the error-state screenshots).
 *
 * Mock-only: no persistence, no isolation, no real model. Never point it at real data.
 */

import * as http from "node:http";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

import { buildSeed, makeId } from "./seed.mjs";

export const MOCK_TOKEN = "demo-token";
const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;

const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
const ms = (iso) => (iso ? Date.parse(iso) : NaN);
const norm = (s) => String(s || "").toLowerCase().replace(/\s+/g, " ").trim();

function send(res, status, body, headers = {}) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers });
  res.end(JSON.stringify(body));
}
const fail = (res, status, detail, reason_code) => send(res, status, reason_code ? { detail, reason_code } : { detail });

function readBody(req, limit = MAX_ATTACHMENT_BYTES + 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (c) => {
      size += c.length;
      if (size > limit) {
        reject(Object.assign(new Error("too large"), { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}
async function readJson(req) {
  const buf = await readBody(req, 1024 * 1024);
  if (!buf.length) return {};
  return JSON.parse(buf.toString("utf8"));
}

/**
 * @param {{processDelayMs?: number}} [options]
 * @returns {{handler: (req, res) => void, state: object}}
 */
export function createMockGateway(options = {}) {
  const processDelayMs = Number(options.processDelayMs ?? 6000);
  const db = buildSeed();
  const sessions = new Map();
  const runs = new Map();
  const idempotency = new Map();
  const artifacts = new Map();
  const failures = new Map();
  let drainRunning = false;
  const PROCESSOR_ID = "au_note_processor";
  const REFLECT_ID = "au_note_reflect";

  // ------------------------------------------------------------ views
  const ent = (id) => db.entities.get(id);
  const entRef = (id) => {
    const e = ent(id);
    return e ? { entity_id: e.entity_id, name: e.name, kind: e.kind } : { entity_id: id, name: id, kind: "thing" };
  };
  // Pinned shapes: docs/api.md "Pinned response shapes".
  const entBrief = (id) => {
    const e = ent(id);
    return { entity_id: e.entity_id, kind: e.kind, name: e.name, aliases: e.aliases, summary: e.summary, first_seen: e.first_seen, last_seen: e.last_seen, note_count: e.note_count, merged_into: null };
  };
  const noteRef = (n) => ({ note_id: n.note_id, title: n.title, kind: n.kind, occurred_at: n.occurred_at, excerpt: n.text.length > 160 ? `${n.text.slice(0, 157)}…` : n.text });
  const insightView = (i) => ({ note_id: i.note_id, title: i.title, body: i.body, insight_kind: i.insight_kind, occurred_at: i.occurred_at, acked_at: i.acked_at, evidence_note_ids: i.evidence_note_ids, entity_ids: i.entity_ids });
  const taskView = (t) => ({ task_id: t.task_id, note_id: t.note_id, text: t.text, status: t.status, due: t.due || null, closed_at: t.closed_at || null });
  const claimView = (c) => {
    const n = db.notes.get(c.note_id);
    // W1 claim_dict: names as subject_name / object_name.
    const out = { ...c, subject_name: entRef(c.subject_id).name, note: n ? { note_id: n.note_id, title: n.title, occurred_at: n.occurred_at } : null };
    if (c.object_id) out.object_name = entRef(c.object_id).name;
    return out;
  };
  const activeAt = (c, at) => {
    if (!at) return c.status === "active";
    const t = ms(at);
    if (c.status === "retracted" && ms(c.retracted_at) <= t) return false;
    return ms(c.valid_from) <= t && (!c.valid_until || ms(c.valid_until) > t);
  };
  const claimsOf = (id) => [...db.claims.values()].filter((c) => c.subject_id === id || c.object_id === id);
  const notesOf = (id) => [...db.notes.values()].filter((n) => n.entity_ids.includes(id)).sort((a, b) => ms(a.occurred_at) - ms(b.occurred_at));
  const inRange = (iso, since, until) => (!since || ms(iso) >= ms(since)) && (!until || ms(iso) <= ms(until));

  function relations() {
    const map = new Map();
    for (const c of db.claims.values()) {
      if (!c.object_id) continue;
      const key = `${c.subject_id}|${c.object_id}|${c.predicate}`;
      const r = map.get(key) || { source: c.subject_id, target: c.object_id, predicate: c.predicate, first_seen: c.observed_at, last_seen: c.observed_at, claims: [] };
      if (ms(c.observed_at) < ms(r.first_seen)) r.first_seen = c.observed_at;
      if (ms(c.observed_at) > ms(r.last_seen)) r.last_seen = c.observed_at;
      r.claims.push(c);
      map.set(key, r);
    }
    return [...map.values()];
  }

  function entityCard(asked, at) {
    // W1 expand.entity_card answers with the surviving entity of a merge (resolved_from = the id asked).
    const id = ent(asked)?.merged_into || asked;
    const e = ent(id);
    if (!e || (at && e.first_seen && ms(e.first_seen) > ms(at))) return null;
    const cl = claimsOf(id).filter((c) => !at || ms(c.observed_at) <= ms(at));
    const beliefs = cl.filter((c) => c.subject_id === id && activeAt(c, at)).map(claimView);
    const rels = relations()
      .filter((r) => (r.source === id || r.target === id) && (!at || ms(r.first_seen) <= ms(at)))
      .map((r) => {
        const other = r.source === id ? r.target : r.source;
        const list = r.claims.filter((c) => !at || ms(c.observed_at) <= ms(at));
        return { other_id: other, other_name: entRef(other).name, predicate: r.predicate, direction: r.source === id ? "out" : "in", count: list.length, first_seen: r.first_seen, last_seen: r.last_seen, active: list.some((c) => activeAt(c, at)) };
      });
    const history = e.summary_history.filter((h) => !at || ms(h.at) <= ms(at));
    const notes = notesOf(id).filter((n) => !at || ms(n.occurred_at) <= ms(at));
    return {
      entity_id: e.entity_id, kind: e.kind, name: e.name, aliases: e.aliases,
      summary: history.length ? history[history.length - 1].summary : e.summary,
      summary_history: history, first_seen: e.first_seen, last_seen: notes.length ? notes[notes.length - 1].occurred_at : e.last_seen,
      note_count: notes.length, beliefs, relations: rels, notes: notes.map(noteRef).reverse(),
      open_tasks: [...db.tasks.values()].filter((t) => t.status === "open" && t.entity_ids.includes(id)).map(taskView),
      as_of: at || null, resolved_from: id !== asked ? asked : null, merged_from: [...db.entities.values()].filter((m) => m.merged_into === id).map((m) => m.entity_id), merged_into: null,
      sparkline: [...notes.reduce((m, n) => m.set(n.occurred_at.slice(0, 7), (m.get(n.occurred_at.slice(0, 7)) || 0) + 1), new Map())].map(([month, n]) => ({ month, notes: n })),
    };
  }

  function thenNow(claimList, history) {
    const sorted = [...claimList].sort((a, b) => ms(a.observed_at) - ms(b.observed_at));
    const turning = sorted.filter((c) => c.supersedes || c.status === "retracted").map((c) => {
      const old = c.supersedes ? db.claims.get(c.supersedes) : null;
      const n = db.notes.get(c.note_id);
      return {
        at: c.observed_at, kind: old ? "supersede" : "retract",
        text: old ? `${entRef(c.subject_id).name} ${predicateWords(c.predicate)}: ${objectName(old)} → ${objectName(c)}` : `${describe(c)} was withdrawn`,
        note_id: c.note_id, claim_id: old ? old.claim_id : c.claim_id, revision_id: c.revision_id,
      };
    });
    const first = sorted[0];
    const last = sorted[sorted.length - 1];
    // W1 then_now: summary points when the entity has summaries, else null with the claims beside them.
    const point = (h) => ({ at: h.at, summary: h.summary, note_id: null, revision_id: null });
    const earliest = history && history.length ? point(history[0]) : null;
    const latest = history && history.length ? point(history[history.length - 1]) : null;
    void first;
    void last;
    return { earliest, latest, turning_points: turning, earliest_claims: sorted.slice(0, 5).map(claimView), current_beliefs: sorted.filter((c) => c.status === "active").map(claimView) };
  }

  const predicateWords = (p) => p.split(":").pop().replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
  const objectName = (c) => (c.object_id ? entRef(c.object_id).name : c.object_literal);
  const describe = (c) => `${entRef(c.subject_id).name} ${predicateWords(c.predicate)} ${objectName(c)}`;

  // ------------------------------------------------------------ search (mock: token overlap, never meaning)
  function search(q, f = {}) {
    const terms = norm(q).split(" ").filter((t) => t.length > 1);
    const score = (text) => terms.reduce((acc, t) => acc + (norm(text).includes(t) ? 1 : 0), 0);
    let notes = [...db.notes.values()].filter((n) => n.status !== "archived");
    if (f.since || f.until) notes = notes.filter((n) => inRange(n.occurred_at, f.since, f.until));
    if (f.kinds?.length) notes = notes.filter((n) => f.kinds.includes(n.kind));
    if (f.topics?.length) notes = notes.filter((n) => n.topics.some((t) => f.topics.includes(t)));
    if (f.entities?.length) notes = notes.filter((n) => f.entities.every((e) => n.entity_ids.includes(e)));
    const hits = terms.length
      ? notes.map((n) => ({ n, s: score(`${n.title} ${n.text}`) })).filter((h) => h.s > 0).sort((a, b) => b.s - a.s || ms(b.n.occurred_at) - ms(a.n.occurred_at))
      : notes.map((n) => ({ n, s: 0 })).sort((a, b) => ms(b.n.occurred_at) - ms(a.n.occurred_at));
    const limit = Number(f.limit || 20);
    const top = hits.slice(0, limit);
    const entities = [...db.entities.values()].filter((e) => terms.length && terms.some((t) => norm([e.name, ...e.aliases].join(" ")).includes(t))).map((e) => ({ ...entRef(e.entity_id), summary: e.summary, last_seen: e.last_seen, note_count: e.note_count }));
    const claims = [...db.claims.values()].filter((c) => terms.length && score(c.evidence) > 0 && c.status === "active").slice(0, 5).map(claimView);
    const facets = { kinds: {}, topics: {}, months: {} };
    for (const { n } of hits) {
      facets.kinds[n.kind] = (facets.kinds[n.kind] || 0) + 1;
      for (const t of n.topics) facets.topics[t] = (facets.topics[t] || 0) + 1;
      const m = n.occurred_at.slice(0, 7);
      facets.months[m] = (facets.months[m] || 0) + 1;
    }
    return { notes: top.map(({ n, s }) => ({ ...noteRef(n), score: s, entities: n.entity_ids.map(entRef) })), entities, claims, facets };
  }

  // ------------------------------------------------------------ processing (mock processor)
  function processNote(n) {
    const linked = [...db.entities.values()].filter((e) => [e.name, ...e.aliases].some((name) => norm(n.text).includes(norm(name))) || (n.about?.kind === "entity" && n.about.id === e.entity_id));
    n.entity_ids = linked.map((e) => e.entity_id);
    const revision_id = makeId("rv", `${n.note_id}:${Date.now()}`);
    const at = nowIso();
    for (const e of linked) {
      e.last_seen = n.occurred_at;
      e.note_count += 1;
    }
    n.status = "processed";
    n.processed_at = at;
    n.revision_id = revision_id;
    db.queue.delete(n.note_id);
    db.revisions.push({ revision_id, note_id: n.note_id, at: n.occurred_at, recorded_at: at, summary: linked.length > 1 ? `Linked ${linked[0].name} to ${linked.slice(1, 3).map((e) => e.name).join(" and ")}.` : linked.length ? `Added to what you know about ${linked[0].name}.` : "Filed with no new links.", ops: [], source: { client: n.source.client } });
  }
  function scheduleProcessing() {
    setTimeout(() => {
      for (const q of db.queue.values()) {
        const n = db.notes.get(q.note_id);
        if (n && n.status === "queued") processNote(n);
      }
    }, processDelayMs);
  }

  // ------------------------------------------------------------ runs
  function newRun(flow_id, session_id, input_data) {
    const run = { run_id: randomUUID(), flow_id, session_id, input_data, status: "running", records: [], output: null, error: null, created_at: nowIso(), listeners: new Set() };
    runs.set(run.run_id, run);
    return run;
  }
  function push(run, effectType, payload, result, node_id) {
    const rec = { step_id: randomUUID(), run_id: run.run_id, node_id: node_id || effectType, status: "completed", effect: { type: effectType, payload }, result, started_at: nowIso(), ended_at: nowIso() };
    run.records.push(rec);
    const cursor = run.records.length;
    for (const l of run.listeners) l.step(cursor, rec);
  }
  function finish(run, output) {
    run.status = "completed";
    run.output = output;
    for (const l of run.listeners) l.done();
  }
  const later = (fn, delay) => setTimeout(fn, delay);

  function startCapture(run) {
    const input = run.input_data || {};
    const key = String(input.idempotency_key || "");
    if (key && idempotency.has(key)) {
      const n = db.notes.get(idempotency.get(key));
      later(() => {
        const response = `Already saved on ${new Date(n.received_at).toUTCString().slice(0, 16)}.`;
        push(run, "tool_calls", { tool_calls: [{ name: "note_capture" }] }, { results: [{ success: true, output: { note_id: n.note_id, status: "duplicate" } }] }, "capture");
        push(run, "answer_user", { message: response }, { message: response, level: "message" }, "answer");
        finish(run, { response, note_id: n.note_id, status: "duplicate" });
      }, 200);
      return;
    }
    if ((input.context?.attachments || []).some((a) => a && typeof a === "object" && "path" in a)) {
      later(() => finish(run, { success: false, output: null, result: null, error: "Runtime error: Local file paths are not accepted here; upload the file as an attachment instead." }), 200);
      return;
    }
    if (input.session_id !== run.session_id) {
      later(() => finish(run, { success: false, output: null, error: "Runtime error: input_data.session_id must equal the run's session." }), 100);
      return;
    }
    const text = String(input.text || "").trim();
    const occurred = String(input.occurred_at || nowIso());
    const note_id = makeId("nt", key || `${norm(text)}|${occurred.slice(0, 16)}|web`);
    if (key) idempotency.set(key, note_id);
    const attachments = (input.context?.attachments || []).map((ref) => artifacts.get(ref?.$artifact)).filter(Boolean).map((a) => ({ attachment_id: makeId("at", a.artifact_id), filename: a.filename, mime: a.content_type, size: a.size, extracted_by: a.content_type.startsWith("text/") ? "plain" : "none", extracted_chars: 0 }));
    const earlier = [...db.notes.values()].filter((o) => norm(o.text) === norm(text)).sort((a, b) => ms(b.occurred_at) - ms(a.occurred_at));
    const sim = search(text, { limit: 4 }).notes.filter((h) => h.score >= 2).slice(0, 3);
    const words = text.replace(/\s+/g, " ").split(" ");
    const title = words.slice(0, 7).join(" ").replace(/[.,;:!?]+$/, "") + (words.length > 7 ? "…" : "");
    const kind = text.trim().endsWith("?") ? "question" : "idea";
    const topics = [...new Set(sim.flatMap((h) => db.notes.get(h.note_id)?.topics || []))].slice(0, 2);
    const n = {
      note_id, kind, title, text, summary: text, occurred_at: occurred, received_at: nowIso(), processed_at: null, status: "queued",
      topics, priority: 1, source: input.source || { client: "web" }, location: input.location || null, attachments,
      entity_ids: [], claim_ids: [], revision_id: null, error: null, session_id: run.session_id, run_id: run.run_id,
      revisits: earlier.map((o) => ({ note_id: o.note_id, occurred_at: o.occurred_at, title: o.title })),
      about: input.context?.about && typeof input.context.about === "object" ? input.context.about : null,
      similar: sim.map((h) => ({ note_id: h.note_id, title: h.title, occurred_at: h.occurred_at, score: Math.min(0.99, 0.8 + h.score / 50) })),
    };
    db.notes.set(note_id, n);
    db.queue.set(note_id, { note_id, priority: 1, attempts: 0, last_error: null, enqueued_at: n.received_at });
    later(() => push(run, "tool_calls", { tool_calls: [{ name: "note_capture" }] }, { results: [{ success: true, output: { note_id, status: "new", revisits: n.revisits, similar: n.similar } }] }, "capture"), 150);
    later(() => push(run, "llm_call", { prompt: "(quick filing)" }, { data: { title, kind, topics } }, "quick_filing"), 500);
    later(() => {
      if (input.process_inline === true) processNote(n);
      const related = n.entity_ids.length ? n.entity_ids.map((id) => entRef(id).name) : sim.map((h) => `"${h.title}"`);
      const response = `Saved as ${kind} "${title}".${related.length ? ` Related: ${related.slice(0, 3).join(", ")}.` : ""}${n.status === "queued" ? " The links come when it is processed." : ""}`;
      push(run, "answer_user", { message: response }, { message: response, level: "message" }, "answer");
      later(() => {
        const out = { response, note_id, status: n.status };
        if (n.status === "processed") out.revision_summary = db.revisions.find((r) => r.revision_id === n.revision_id)?.summary;
        finish(run, out);
        if (n.status === "queued") scheduleProcessing();
      }, 400);
    }, 900);
  }

  function startRecall(run) {
    const input = run.input_data || {};
    const question = String(input.question || input.prompt || "");
    const effort = ["quick", "standard", "deep"].includes(input.effort) ? input.effort : "standard";
    const hits = search(question, { since: input.since, until: input.until, entities: input.entities, topics: input.topics, limit: 6 });
    later(() => push(run, "tool_calls", { tool_calls: [{ name: "note_search" }] }, { results: [{ success: true, output: { count: hits.notes.length } }] }, "search"), 300);
    if (effort === "quick") {
      later(() => finish(run, { response: `${hits.notes.length} notes match.`, hits }), 600);
      return;
    }
    const evidence = hits.notes.slice(0, 4).map((h) => ({ note_id: h.note_id, title: h.title, kind: h.kind, occurred_at: h.occurred_at, excerpt: h.excerpt }));
    const topEntity = hits.entities[0] ? ent(hits.entities[0].entity_id) : null;
    const sentences = evidence.slice(0, 3).map((e) => `On ${new Date(e.occurred_at).toUTCString().slice(5, 16)} you noted "${e.title}" [${e.note_id}].`);
    let evolution = { earliest: null, latest: null, turning_points: [] };
    if (topEntity) {
      evolution = thenNow(claimsOf(topEntity.entity_id), topEntity.summary_history);
      // The notes behind the turning points are evidence too (the answer cites them).
      for (const t of evolution.turning_points) {
        const n = db.notes.get(t.note_id);
        if (n && !evidence.some((e) => e.note_id === n.note_id)) evidence.push(noteRef(n));
      }
      if (evolution.turning_points.length) sentences.push(`What changed: ${evolution.turning_points.map((t) => `${t.text} [${t.note_id}]`).join("; ")}.`);
    }
    const answer = evidence.length ? sentences.join(" ") : "Your notes do not mention this yet.";
    const open_loops = [...db.tasks.values()].filter((t) => t.status === "open" && (!topEntity || t.entity_ids.includes(topEntity.entity_id))).slice(0, 3);
    const follow_ups = topEntity ? [`What did I believe about ${topEntity.name} in August?`, `Who else is linked to ${topEntity.name}?`] : [];
    later(() => push(run, "tool_calls", { tool_calls: [{ name: "note_expand" }] }, { results: [{ success: true, output: {} }] }, "expand"), 800);
    const delay = effort === "deep" ? 6000 : 1800;
    later(() => {
      push(run, "llm_call", { prompt: "(synthesis)" }, { data: { answer } }, "synthesize");
      const out = { response: answer, answer, evidence, evolution, open_loops, follow_ups };
      if (effort === "deep") {
        const note_id = makeId("nt", `dossier-${run.run_id}`);
        db.insights.set(note_id, { note_id, kind: "synthesis", insight_kind: "dossier", title: `Dossier: ${question.slice(0, 60)}`, body: answer, occurred_at: nowIso(), evidence_note_ids: evidence.map((e) => e.note_id), entity_ids: topEntity ? [topEntity.entity_id] : [], acked_at: null });
        out.dossier_note_id = note_id;
      }
      finish(run, out);
    }, delay);
  }

  // ------------------------------------------------------------ routes
  function sse(req, res, run, after) {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    const step = (cursor, rec) => res.write(`id: ${cursor}\nevent: step\ndata: ${JSON.stringify({ cursor, record: rec })}\n\n`);
    const done = () => {
      res.write(`event: done\ndata: ${JSON.stringify({ run_id: run.run_id, cursor: run.records.length, status: "terminal" })}\n\n`);
      res.end();
      run.listeners.delete(listener);
    };
    const listener = { step, done };
    run.records.slice(after).forEach((rec, i) => step(after + i + 1, rec));
    if (run.status !== "running") return done();
    run.listeners.add(listener);
    req.on("close", () => run.listeners.delete(listener));
  }

  function principal(req) {
    const sid = String(req.headers["x-abstractgateway-session"] || "");
    if (sid && sessions.has(sid)) return sessions.get(sid);
    const auth = String(req.headers.authorization || "");
    if (auth === `Bearer ${MOCK_TOKEN}`) return { user_id: "demo", csrf: null, bearer: true };
    return null;
  }

  async function route(req, res, url) {
    const p = url.pathname;
    const q = url.searchParams;
    const method = req.method || "GET";

    if (p === "/__mock/fail" && method === "POST") {
      const body = await readJson(req);
      failures.set(String(body.path), { count: Number(body.count || 3), status: Number(body.status || 503) });
      return send(res, 200, { ok: true });
    }
    if (p === "/api/health") return send(res, 200, { ok: true });
    if (p === "/api/gateway/about") return send(res, 200, { abstractgateway: "mock", abstractframework: null, packages: {} });
    if (p === "/api/gateway/session/login" && method === "POST") {
      const body = await readJson(req);
      if (String(body.token || "") !== MOCK_TOKEN) return fail(res, 401, "That token was not accepted.");
      const session_id = randomUUID();
      const csrf_token = randomUUID();
      const user_id = String(body.user_id || "demo") || "demo";
      sessions.set(session_id, { user_id, csrf: csrf_token });
      return send(res, 200, { ok: true, session: { session_id, csrf_token }, principal: { user_id } }, {
        "Set-Cookie": [`abstractgateway_session=${session_id}; Path=/; HttpOnly`, `abstractgateway_csrf=${csrf_token}; Path=/`],
      });
    }

    const who = principal(req);
    if (!who) return fail(res, 401, "Sign-in required.");
    if (!["GET", "HEAD"].includes(method) && !who.bearer && String(req.headers["x-abstractgateway-csrf"] || "") !== who.csrf) return fail(res, 403, "CSRF token missing.", "csrf_required");

    const injected = failures.get(p);
    if (injected && injected.count > 0) {
      injected.count -= 1;
      return fail(res, injected.status, "Injected failure.");
    }

    if (p === "/api/gateway/me") return send(res, 200, { ok: true, principal: { user_id: who.user_id, admin: false } });
    if (p === "/api/gateway/session/logout") {
      for (const [sid, s] of sessions) if (s === who) sessions.delete(sid);
      return send(res, 200, { ok: true });
    }
    if (p === "/api/gateway/discovery/providers") return send(res, 200, { providers: [{ name: "mlx", display_name: "MLX" }, { name: "lmstudio", display_name: "LM Studio" }, { name: "openai", display_name: "OpenAI" }] });
    let m = p.match(/^\/api\/gateway\/discovery\/providers\/([^/]+)\/models$/);
    if (m) {
      const models = { mlx: ["mlx-community/Qwen3.6-35B-A3B-4bit", "mlx-community/Qwen3.6-9B-4bit"], lmstudio: ["qwen/qwen3.6-27b"], openai: ["gpt-5-mini"] }[decodeURIComponent(m[1])] || [];
      return send(res, 200, { models });
    }

    if (p === "/api/gateway/attachments/upload" && method === "POST") {
      let buf;
      try {
        buf = await readBody(req);
      } catch (err) {
        return fail(res, err.status || 400, "File too large.");
      }
      const head = buf.subarray(0, Math.min(buf.length, 4096)).toString("latin1");
      const filename = (head.match(/name="file"; filename="([^"]*)"/) || [])[1] || "file";
      const content_type = (head.match(/Content-Type: ([^\r\n]+)/i) || [])[1] || "application/octet-stream";
      const size = Math.max(0, buf.length - 400);
      if (size > MAX_ATTACHMENT_BYTES) return fail(res, 413, "File too large.");
      const artifact_id = randomUUID().replace(/-/g, "");
      artifacts.set(artifact_id, { artifact_id, filename, content_type, size });
      return send(res, 200, { artifact: { $artifact: artifact_id, artifact_id, run_id: "session_memory_mock", content_type, filename } });
    }

    if (p === "/api/gateway/runs/start" && method === "POST") {
      const body = await readJson(req);
      if (body.bundle_id !== "abstractnote") return fail(res, 404, "Unknown bundle.");
      const run = newRun(body.flow_id, body.session_id, body.input_data);
      if (body.flow_id === "note-capture") startCapture(run);
      else if (body.flow_id === "note-recall") startRecall(run);
      else return fail(res, 404, "Unknown flow.");
      return send(res, 200, { run_id: run.run_id });
    }
    m = p.match(/^\/api\/gateway\/runs\/([^/]+)(\/ledger(\/stream)?)?$/);
    if (m) {
      const run = runs.get(m[1]);
      if (!run) return fail(res, 404, "Not found.");
      const after = Number(q.get("after") || req.headers["last-event-id"] || 0);
      if (m[3]) return sse(req, res, run, after);
      if (m[2]) return send(res, 200, { items: run.records.slice(after).map((record, i) => ({ cursor: after + i + 1, record })), next_after: run.records.length });
      return send(res, 200, { run_id: run.run_id, status: run.status, output: run.output, error: run.error, waiting: null });
    }
    m = p.match(/^\/api\/gateway\/automations\/([^/]+)\/commands$/);
    if (m && method === "POST") {
      const body = await readJson(req);
      if (m[1] !== PROCESSOR_ID) return fail(res, 404, "Not found.");
      if (body.type !== "automation.run_now" || typeof body.command_id !== "string") return fail(res, 400, "Unknown command.");
      if (drainRunning) return send(res, 409, { detail: { reason_code: "automation_busy", message: "An occurrence is already running or waiting to run.", command_id: body.command_id } });
      drainRunning = true;
      setTimeout(() => {
        for (const item of db.queue.values()) {
          const n = db.notes.get(item.note_id);
          if (n && n.status === "queued") processNote(n);
        }
        drainRunning = false;
      }, 4000);
      return send(res, 200, { command_id: body.command_id, accepted: true, duplicate: false, seq: 1 });
    }

    if (!p.startsWith("/api/gateway/note/")) return fail(res, 404, "Not found.");
    const np = p.slice("/api/gateway/note".length);
    const at = q.get("at") || null;
    const list = (s) => (q.get(s) ? q.get(s).split(",").filter(Boolean) : []);

    if (np === "/status") {
      const counts = { notes: db.notes.size, entities: db.entities.size, claims: db.claims.size, revisions: db.revisions.length, insights: db.insights.size };
      return send(res, 200, { counts, queue: queueStats(), settings: db.settings, automations: { processor_id: PROCESSOR_ID, reflect_id: REFLECT_ID }, embedding_pin: { provider: "mock", model: "mock-embed", dims: 8 } });
    }
    if (np === "/changes") {
      const since = q.get("since") || new Date(Date.now() - 7 * 86400_000).toISOString();
      const t = ms(since);
      const fresh = (iso) => ms(iso) > t;
      return send(res, 200, {
        cursor: nowIso(),
        notes: [...db.notes.values()].filter((n) => fresh(n.received_at) && n.status !== "failed").sort((a, b) => ms(b.received_at) - ms(a.received_at)).map(noteRef),
        entities: [...db.entities.values()].filter((e) => e.created_at && fresh(e.created_at)).map((e) => ({ ...entRef(e.entity_id), first_seen: e.first_seen })),
        superseded_claims: [...db.claims.values()].filter((c) => c.status !== "active" && fresh(c.valid_until || c.retracted_at || c.recorded_at)).map((c) => ({ claim_id: c.claim_id, kind: c.status === "superseded" ? "superseded" : "retracted", text: describe(c), superseded_by: c.status === "superseded" ? c.superseded_by : null, note_id: c.note_id })),
        closed_loops: [...db.tasks.values()].filter((tk) => tk.status !== "open" && tk.closed_at && fresh(tk.closed_at)).map((tk) => ({ task_id: tk.task_id, text: tk.text, closed_at: tk.closed_at })),
        failed_notes: [...db.notes.values()].filter((n) => n.status === "failed").map((n) => ({ note_id: n.note_id, title: n.title, error: n.error })),
        insights: [...db.insights.values()].filter((i) => fresh(i.occurred_at)).map(insightView),
      });
    }
    if (np === "/overview") {
      const days = Number(q.get("days") || 30);
      const from = Date.now() - days * 86400_000;
      const recent = [...db.notes.values()].sort((a, b) => ms(b.occurred_at) - ms(a.occurred_at));
      const activity = new Map();
      for (const n of recent) if (ms(n.occurred_at) >= from) {
        const day = n.occurred_at.slice(0, 10);
        const a = activity.get(day) || { day, notes: 0, revisions: 0 };
        a.notes += 1;
        if (n.revision_id) a.revisions += 1;
        activity.set(day, a);
      }
      const resurface = [makeId("nt", "seed-8"), makeId("nt", "seed-4")].map((id) => db.notes.get(id)).filter(Boolean);
      return send(res, 200, {
        recent: recent.slice(0, 8).map(noteRef),
        entities: [...db.entities.values()].filter((e) => ms(e.last_seen) >= from).sort((a, b) => ms(b.last_seen) - ms(a.last_seen)).map((e) => ({ ...entRef(e.entity_id), note_count: e.note_count, last_seen: e.last_seen })),
        insights: [...db.insights.values()].filter((i) => !i.acked_at).map(insightView),
        resurfacing: resurface.map(noteRef),
        activity: [...activity.values()].sort((a, b) => a.day.localeCompare(b.day)),
      });
    }
    if (np === "/notes" && method === "GET") {
      let items = [...db.notes.values()];
      if (q.get("status")) items = items.filter((n) => n.status === q.get("status"));
      if (q.get("kind")) items = items.filter((n) => n.kind === q.get("kind"));
      if (q.get("entity")) items = items.filter((n) => n.entity_ids.includes(q.get("entity")));
      items = items.filter((n) => inRange(n.occurred_at, q.get("since"), q.get("until")));
      items.sort((a, b) => ms(b.occurred_at) - ms(a.occurred_at));
      return send(res, 200, { items: items.slice(0, Number(q.get("limit") || 50)).map(noteRef), next_cursor: null });
    }
    m = np.match(/^\/notes\/([^/]+)$/);
    if (m && method === "GET") {
      const n = db.notes.get(m[1]) || db.insights.get(m[1]);
      if (!n) return fail(res, 404, "Not found.");
      if (!n.text) {
        // W10: an insight note's read carries `insight` {kind, evidence_note_ids, entity_ids, acked_at} (null for other notes).
        return send(res, 200, { note_id: n.note_id, title: n.title, text: n.body, kind: n.kind || "insight", occurred_at: n.occurred_at, received_at: n.occurred_at, status: "processed", topics: [], summary: n.body, source: { client: "agent" }, location: null, attachments: [], revision: null, entities: (n.entity_ids || []).map(entRef), claims: [], revisits: [], insight: { kind: n.insight_kind, evidence_note_ids: n.evidence_note_ids || [], entity_ids: n.entity_ids || [], acked_at: n.acked_at || null } });
      }
      const rev = db.revisions.find((r) => r.revision_id === n.revision_id) || null;
      return send(res, 200, {
        note_id: n.note_id, title: n.title, text: n.text, kind: n.kind, occurred_at: n.occurred_at, received_at: n.received_at,
        status: n.status, topics: n.topics, summary: n.summary, source: n.source, location: n.location, attachments: n.attachments,
        revision: rev ? { revision_id: rev.revision_id, summary: rev.summary, at: rev.at, recorded_at: rev.recorded_at } : null,
        entities: n.entity_ids.map(entRef),
        claims: n.claim_ids.map((id) => claimView(db.claims.get(id))),
        revisits: (n.revisits || []).map((r) => db.notes.get(r.note_id)).filter(Boolean).map(noteRef),
        insight: null,
      });
    }
    m = np.match(/^\/notes\/([^/]+)\/attachments\/([^/]+)$/);
    if (m) {
      res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
      return res.end("Mock attachment bytes.");
    }
    m = np.match(/^\/notes\/([^/]+)\/archive$/);
    if (m && method === "POST") {
      const n = db.notes.get(m[1]);
      if (!n) return fail(res, 404, "Not found.");
      n.status = "archived";
      n.archived_at = nowIso();
      return send(res, 200, { note_id: n.note_id, status: n.status });
    }
    if (np === "/search" && !(q.get("q") || "").trim()) return fail(res, 422, "q is required.");
    if (np === "/search") return send(res, 200, search(q.get("q") || "", { since: q.get("since"), until: q.get("until"), kinds: list("kinds"), entities: list("entities"), topics: list("topics"), limit: q.get("limit") }));
    if (np === "/entities") {
      const term = norm(q.get("q"));
      let items = [...db.entities.values()].filter((e) => !e.merged_into && (!term || norm([e.name, ...e.aliases].join(" ")).includes(term)));
      if (q.get("kind")) items = items.filter((e) => e.kind === q.get("kind"));
      const sort = q.get("sort") || "recent";
      items.sort(sort === "name" ? (a, b) => a.name.localeCompare(b.name) : sort === "frequent" ? (a, b) => b.note_count - a.note_count : (a, b) => ms(b.last_seen) - ms(a.last_seen));
      return send(res, 200, { items: items.slice(0, Number(q.get("limit") || 50)).map((e) => ({ ...entRef(e.entity_id), aliases: e.aliases, summary: e.summary, first_seen: e.first_seen, last_seen: e.last_seen, note_count: e.note_count })), next_cursor: null });
    }
    m = np.match(/^\/entities\/([^/]+)(\/timeline|\/then-now|\/unmerge)?$/);
    if (m) {
      const id = decodeURIComponent(m[1]);
      const e = ent(id);
      if (!e) return fail(res, 404, "Not found.");
      if (!m[2]) {
        const card = entityCard(id, at);
        if (!card) return fail(res, 404, "Not known at that time.");
        return send(res, 200, card);
      }
      if (m[2] === "/timeline") {
        const items = [
          ...claimsOf(id).map((c) => ({ type: "claim", at: c.observed_at, claim: claimView(c) })),
          ...notesOf(id).map((n) => ({ type: "note", at: n.occurred_at, note: noteRef(n) })),
        ].filter((it) => inRange(it.at, q.get("since"), q.get("until"))).sort((a, b) => ms(a.at) - ms(b.at));
        return send(res, 200, { entity: entRef(id), items });
      }
      if (m[2] === "/then-now") return send(res, 200, { entity_id: id, revision_count: notesOf(id).length, ...thenNow(claimsOf(id), e.summary_history) });
      if (method !== "POST") return fail(res, 405, "Method not allowed.");
      const body = await readJson(req);
      const from = ent(String(body.from_id || ""));
      if (!from) return fail(res, 404, "Not found.");
      if (from.merged_into !== id) return fail(res, 400, `${from.name} is not joined to ${e.name}.`);
      from.merged_into = null;
      const when = nowIso();
      const revision_id = makeId("rv", `unmerge:${from.entity_id}:${when}`);
      db.revisions.push({ revision_id, note_id: null, at: when, recorded_at: when, summary: `You separated ${from.name} from ${e.name}.`, ops: [{ op: "entity.unmerge", entity_id: id, from_id: from.entity_id, reason: String(body.reason || "") }], source: { client: "web", account: who.user_id } });
      return send(res, 200, { revision_id });
    }
    m = np.match(/^\/relations\/([^/]+)\/([^/]+)$/);
    if (m) {
      const a = decodeURIComponent(m[1]);
      const b = decodeURIComponent(m[2]);
      if (!ent(a) || !ent(b)) return fail(res, 404, "Not found.");
      const between = [...db.claims.values()].filter((c) => (c.subject_id === a && c.object_id === b) || (c.subject_id === b && c.object_id === a)).filter((c) => inRange(c.observed_at, q.get("since"), q.get("until"))).sort((x, y) => ms(x.observed_at) - ms(y.observed_at));
      const shared = [...db.notes.values()].filter((n) => n.entity_ids.includes(a) && n.entity_ids.includes(b)).sort((x, y) => ms(x.occurred_at) - ms(y.occurred_at));
      // W1 expand.relation: claims in history order, supersessions, then_now with claim briefs.
      void shared;
      const brief = (c) => ({ claim_id: c.claim_id, text: `${entRef(c.subject_id).name} ${c.predicate} ${objectName(c)}`, observed_at: c.observed_at, note_id: c.note_id, certainty: c.certainty });
      const closed = between.filter((c) => c.status !== "active");
      const firstAt = between.length ? between[0].observed_at : null;
      return send(res, 200, {
        a: entBrief(a), b: entBrief(b), since: q.get("since"), until: q.get("until"), claims: between.map(claimView),
        supersessions: closed.map((c) => ({ claim_id: c.claim_id, superseded_by: c.superseded_by, at: c.valid_until, kind: c.status === "retracted" ? "retract" : "supersede", note_id: c.superseded_by ? db.claims.get(c.superseded_by)?.note_id : null })),
        then_now: {
          earliest: between.filter((c) => c.observed_at === firstAt).map(brief),
          latest: between.filter((c) => c.status === "active").map(brief),
          turning_points: closed.map((c) => ({ at: c.valid_until, kind: c.status === "retracted" ? "retract" : "supersede", claim_id: c.claim_id, superseded_by: c.superseded_by, note_id: c.superseded_by ? db.claims.get(c.superseded_by)?.note_id : null, reason: "" })),
        },
      });
    }
    m = np.match(/^\/claims\/([^/]+)(\/retract)?$/);
    if (m) {
      const c = db.claims.get(m[1]);
      if (!c) return fail(res, 404, "Not found.");
      if (m[2]) {
        if (method !== "POST") return fail(res, 405, "Method not allowed.");
        const body = await readJson(req);
        c.status = "retracted";
        c.retracted_at = nowIso();
        c.valid_until = c.valid_until || c.retracted_at;
        const revision_id = makeId("rv", `retract:${c.claim_id}:${c.retracted_at}`);
        db.revisions.push({ revision_id, note_id: null, at: c.retracted_at, recorded_at: c.retracted_at, summary: `You withdrew: ${describe(c)}.`, ops: [{ op: "claim.retract", claim_id: c.claim_id, reason: body.reason || "" }], source: { client: "web", account: who.user_id } });
        return send(res, 200, { revision_id, claim: claimView(c) });
      }
      const n = db.notes.get(c.note_id);
      const supersedes = c.supersedes ? [claimView(db.claims.get(c.supersedes))] : [];
      const rev = db.revisions.find((r) => r.revision_id === c.revision_id) || null;
      // W1 claim chain: attachments sit under their note.
      return send(res, 200, {
        claim: claimView(c), supersedes, superseded_by: c.superseded_by ? claimView(db.claims.get(c.superseded_by)) : null, disputed_with: [],
        notes: n ? [{ ...noteRef(n), attachments: n.attachments.map((a) => ({ ...a, note_id: n.note_id, extracted_text: "Phase 1 prefill: 1,920 tokens/s against a 1,900 baseline; quality gate failed on 3 of 12 prompts." })) }] : [],
        revision: rev ? { revision_id: rev.revision_id, summary: rev.summary, at: rev.at } : null,
      });
    }
    m = np.match(/^\/tasks\/([^/]+)\/close$/);
    if (m && method === "POST") {
      const t = db.tasks.get(m[1]);
      if (!t) return fail(res, 404, "Not found.");
      t.status = "done";
      t.closed_at = nowIso();
      return send(res, 200, { task: taskView(t) });
    }
    if (np === "/tasks") {
      const status = q.get("status") || "open";
      let items = [...db.tasks.values()].filter((t) => status === "all" || t.status === status);
      if (q.get("entity")) items = items.filter((t) => t.entity_ids.includes(q.get("entity")));
      return send(res, 200, { items: items.sort((a, b) => ms(a.opened_at) - ms(b.opened_at)).map(taskView), next_cursor: null });
    }
    if (np === "/cues") {
      // W1 recall/changes.cues: the unacknowledged insights other than dossiers, newest first; the subject is
      // the first linked entity, else the first evidence note, else the insight itself.
      const items = [...db.insights.values()].filter((i) => !i.acked_at && i.insight_kind !== "dossier").sort((a, b) => ms(b.occurred_at) - ms(a.occurred_at)).slice(0, Number(q.get("limit") || 3)).map((i) => {
        const e = i.entity_ids.map(ent).find(Boolean);
        const n = i.evidence_note_ids.map((id) => db.notes.get(id)).find(Boolean);
        const subject = e ? { kind: "entity", id: e.entity_id, title: e.name } : n ? { kind: "note", id: n.note_id, title: n.title } : { kind: "cue", id: i.note_id, title: i.title };
        // W10: a cue carries its insight's evidence_note_ids and entity_ids (why it surfaced, without a second read).
        return { cue_id: i.note_id, text: i.body || i.title, kind: i.insight_kind, subject, evidence_note_ids: i.evidence_note_ids, entity_ids: i.entity_ids };
      });
      return send(res, 200, { items });
    }
    if (np === "/graph") return send(res, 200, graph(list("focus"), at, Number(q.get("depth") || 1), Number(q.get("limit") || 60)));
    if (np === "/timeline") {
      const gran = q.get("granularity") || "week";
      const bucket = (iso) => {
        const d = new Date(iso);
        if (gran === "day") return iso.slice(0, 10);
        if (gran === "month") return iso.slice(0, 7) + "-01";
        const day = (d.getUTCDay() + 6) % 7;
        return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - day)).toISOString().slice(0, 10);
      };
      const map = new Map();
      const add = (iso, field) => {
        if (!inRange(iso, q.get("since"), q.get("until"))) return;
        const k = bucket(iso);
        const b = map.get(k) || { bucket: gran === "month" ? k.slice(0, 7) : k, notes: 0, revisions: 0, entities_created: 0, claims_superseded: 0 };
        b[field] += 1;
        map.set(k, b);
      };
      for (const n of db.notes.values()) add(n.occurred_at, "notes");
      for (const r of db.revisions) add(r.at, "revisions");
      for (const e of db.entities.values()) if (e.created_at) add(e.created_at, "entities_created");
      for (const c of db.claims.values()) if (c.valid_until) add(c.valid_until, "claims_superseded");
      return send(res, 200, { granularity: gran, since: q.get("since"), until: q.get("until"), buckets: [...map.values()].sort((a, b) => a.bucket.localeCompare(b.bucket)) });
    }
    m = np.match(/^\/day\/(\d{4}-\d{2}-\d{2})$/);
    if (m) {
      const day = m[1];
      const onDay = (iso) => iso && iso.slice(0, 10) === day;
      const notes = [...db.notes.values()].filter((n) => onDay(n.occurred_at)).map(noteRef);
      const revs = db.revisions.filter((r) => onDay(r.at)).map((r) => ({ revision_id: r.revision_id, at: r.at, summary: r.summary, note_id: r.note_id }));
      const touched = new Map();
      for (const n of db.notes.values()) if (onDay(n.occurred_at)) for (const id of n.entity_ids) touched.set(id, { ...entRef(id), change: ent(id).created_at && onDay(ent(id).created_at) ? "created" : "touched" });
      const superseded = [...db.claims.values()].filter((c) => onDay(c.valid_until) && c.status === "superseded").map(claimView);
      const created = [...db.entities.values()].filter((e) => onDay(e.created_at)).map((e) => e.entity_id);
      return send(res, 200, { day, tz: q.get("tz") || "UTC", notes, revisions: revs, entities_touched: [...touched.values()].map(({ change, ...e }) => e), entities_created: created, claims_superseded: superseded });
    }
    if (np === "/revisions") return send(res, 200, { items: [...db.revisions].sort((a, b) => ms(b.recorded_at) - ms(a.recorded_at)).slice(0, Number(q.get("limit") || 50)), next_cursor: null });
    if (np === "/topics") {
      const map = new Map();
      for (const n of db.notes.values()) for (const t of n.topics) {
        const r = map.get(t) || { topic: t, count: 0, first_seen: n.occurred_at, last_seen: n.occurred_at };
        r.count += 1;
        if (ms(n.occurred_at) < ms(r.first_seen)) r.first_seen = n.occurred_at;
        if (ms(n.occurred_at) > ms(r.last_seen)) r.last_seen = n.occurred_at;
        map.set(t, r);
      }
      return send(res, 200, { items: [...map.values()].sort((a, b) => b.count - a.count) });
    }
    m = np.match(/^\/insights\/([^/]+)\/ack$/);
    if (m && method === "POST") {
      const i = db.insights.get(m[1]);
      if (!i) return fail(res, 404, "Not found.");
      i.acked_at = nowIso();
      return send(res, 200, { note_id: i.note_id, acked_at: i.acked_at });
    }
    if (np === "/insights") {
      let items = [...db.insights.values()];
      if (q.get("acked") === "false") items = items.filter((i) => !i.acked_at);
      if (q.get("acked") === "true") items = items.filter((i) => i.acked_at);
      return send(res, 200, { items: items.sort((a, b) => ms(b.occurred_at) - ms(a.occurred_at)).map(insightView), next_cursor: null });
    }
    if (np === "/queue") {
      // W1 Queue.summary + W2 automations.
      const failed = [...db.notes.values()].filter((n) => n.status === "failed").map((n) => ({ note_id: n.note_id, last_error: n.error, failed_at: n.received_at, failures: 3, title: n.title, occurred_at: n.occurred_at }));
      const waiting = [...db.notes.values()].filter((n) => n.status === "queued").map((n) => ({ note_id: n.note_id, priority: 1, state: "queued", attempts: 0, next_attempt_at: null, title: n.title, occurred_at: n.occurred_at }));
      return send(res, 200, { stats: queueStats(), failed, waiting, automations: { processor_id: PROCESSOR_ID, reflect_id: REFLECT_ID } });
    }
    m = np.match(/^\/queue\/([^/]+)\/retry$/);
    if (m && method === "POST") {
      const n = db.notes.get(m[1]);
      if (!n || n.status !== "failed") return fail(res, 404, "Not found.");
      n.status = "queued";
      n.error = null;
      db.queue.set(n.note_id, { note_id: n.note_id, priority: 1, attempts: 0, last_error: null, enqueued_at: nowIso() });
      scheduleProcessing();
      return send(res, 200, { note_id: n.note_id, status: n.status });
    }
    if (np === "/settings") {
      if (method === "PUT") {
        const body = await readJson(req);
        const unknown = Object.keys(body).filter((k) => !(k in db.settings));
        if (unknown.length) return fail(res, 400, `Unknown setting: ${unknown.join(", ")}.`);
        Object.assign(db.settings, body);
        return send(res, 200, db.settings);
      }
      return send(res, 200, db.settings);
    }
    if (np === "/processor/ensure" && method === "POST") return send(res, 200, { processor_id: PROCESSOR_ID, reflect_id: REFLECT_ID, created: [] });
    return fail(res, 404, "Not found.");
  }

  function queueStats() {
    const notes = [...db.notes.values()];
    const queued = notes.filter((n) => n.status === "queued");
    return {
      queued: queued.length,
      processing: drainRunning ? Math.min(1, queued.length) : 0,
      failed: notes.filter((n) => n.status === "failed").length,
      done: notes.filter((n) => n.status === "processed").length,
      by_priority: { 3: 0, 2: 0, 1: queued.length, 0: 0 },
      oldest_enqueued_at: queued.length ? queued.map((n) => n.received_at).sort()[0] : null,
    };
  }

  function graph(focus, at, depth, limit) {
    const known = (e) => e && !e.merged_into && (!at || (e.first_seen && ms(e.first_seen) <= ms(at)));
    const rels = relations().filter((r) => !at || ms(r.first_seen) <= ms(at));
    let ids;
    if (focus.length) {
      ids = new Set(focus.filter((id) => known(ent(id))));
      let frontier = [...ids];
      for (let d = 0; d < Math.max(1, Math.min(3, depth)); d++) {
        const next = [];
        for (const r of rels) {
          if (frontier.includes(r.source) && !ids.has(r.target)) next.push(r.target);
          if (frontier.includes(r.target) && !ids.has(r.source)) next.push(r.source);
        }
        for (const id of next) if (known(ent(id))) ids.add(id);
        frontier = next;
      }
    } else {
      ids = new Set([...db.entities.values()].filter(known).map((e) => e.entity_id));
    }
    const nodes = [...ids].slice(0, limit).map((id) => {
      const e = ent(id);
      const notes = notesOf(id).filter((n) => !at || ms(n.occurred_at) <= ms(at));
      return { id, kind: e.kind, name: e.name, first_seen: e.first_seen, last_seen: notes.length ? notes[notes.length - 1].occurred_at : e.first_seen, note_count: notes.length, summary: e.summary };
    });
    const nodeIds = new Set(nodes.map((n) => n.id));
    const edges = rels.filter((r) => nodeIds.has(r.source) && nodeIds.has(r.target)).map((r) => {
      const cl = r.claims.filter((c) => !at || ms(c.observed_at) <= ms(at));
      return { source: r.source, target: r.target, predicate: r.predicate, first_seen: r.first_seen, last_seen: r.last_seen, count: cl.length, active: cl.some((c) => activeAt(c, at)) };
    });
    return { as_of: at || nowIso(), nodes, edges };
  }

  async function handler(req, res) {
    const url = new URL(req.url || "/", "http://mock.invalid");
    try {
      await route(req, res, url);
    } catch (err) {
      if (!res.headersSent) fail(res, err instanceof SyntaxError ? 400 : 500, err instanceof SyntaxError ? "The request body is not valid JSON." : String(err?.message || err));
    }
  }

  return { handler, state: { db, runs, sessions } };
}

function parseArgs(argv) {
  const out = { port: 18890, host: "127.0.0.1", processDelayMs: 6000 };
  for (let i = 0; i < argv.length; i++) {
    const [k, inline] = argv[i].split("=");
    const v = inline ?? argv[i + 1];
    if (k === "--port") out.port = Number(v);
    else if (k === "--host") out.host = v;
    else if (k === "--process-delay-ms") out.processDelayMs = Number(v);
    else continue;
    if (inline === undefined) i++;
  }
  return out;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const args = parseArgs(process.argv.slice(2));
  const { handler } = createMockGateway({ processDelayMs: args.processDelayMs });
  http.createServer(handler).listen(args.port, args.host, () => {
    console.log(`Mock gateway for AbstractNote on http://${args.host}:${args.port} (sign in with token ${MOCK_TOKEN})`);
  });
  for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => process.exit(0));
}
