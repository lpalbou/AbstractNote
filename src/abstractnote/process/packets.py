"""What a model call sees about one note: bounded, byte-stable prompt packets (backlog 0024).

Every prompt is a STATIC system prompt (instructions, vocabulary, the output shape, rules:
identical bytes for every note, so the local model's prompt cache reuses it) plus a DYNAMIC user
message built here: the note's own envelope, a one-line header, and a bounded view of what the
notebook already knows. Only the packet is bounded; the note's text is never cut.

These functions run in two places with one source: in Python (tests, the CLI) and inside the
flows' sandboxed code nodes, whose bodies ``abstractnote.flows.build`` assembles from this
module's source. So they use only the standard library, import inside the function, and use no
names that start with an underscore (the sandbox refuses them).
"""

from __future__ import annotations

from typing import Any, Dict, List

#: Mentions decided per linking call; the rest go to the next call for the same note.
LINK_MENTIONS_PER_CALL = 15
#: Existing entities shown per mention (``note_resolve_candidates`` returns at most this many).
CANDIDATES_PER_MENTION = 3
#: Active claims shown per candidate entity, one line each.
CLAIMS_PER_CANDIDATE = 3
#: Orientation lists in the extraction packet.
KNOWN_NAMES = 15
SIMILAR_NOTES = 5
OPEN_TASKS = 8
TOPICS = 50  # the most used existing topics (eval2 D13), the note's own first
#: Predicates of the known entities' current statements listed for extraction (eval2 D6: the same fact keeps
#: the same predicate, so a later value can replace it), the most used first.
KNOWN_PREDICATES = 15
#: Statement slots (subject, predicate spelling, value) of the known entities listed for extraction, so a note
#: about the same slot reuses its spelling (the linking then names the statement by index, never by string).
KNOWN_SLOTS = 30
#: Claims recorded LATER than the note shown per candidate (ADR 0008 §2), the earliest later first: never the
#: CLAIMS_PER_CANDIDATE cut; what the budget leaves out is said on the card and recorded in the digest.
LATER_CLAIMS_PER_CANDIDATE = 12
#: Summaries of a later day shown per candidate, the latest ones.
LATER_SUMMARIES_PER_CANDIDATE = 2
#: The LATER block's label (ADR 0008 §2): what the model may do with what was recorded after the note.
LATER_LABEL = ("LATER (recorded after this note's date — do not restate; say if a statement is an earlier state of"
               " one of these):")
#: Characters kept of a summary, an evidence excerpt or a role in a packet line.
SUMMARY_CHARS = 240
EVIDENCE_CHARS = 200


def clip(text: Any, limit: int) -> str:
    """One line of at most ``limit`` characters, cut at a word boundary (packet views only)."""
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0]
    return (cut or s[:limit]).rstrip(" ,;:") + "…"


def later_than(when: Any, as_of: Any) -> bool:
    """Whether a date falls on a later DAY than ``as_of`` (the note's date): what was recorded after the note.
    Live notes minutes apart are the same day, never later. False when either date is missing."""
    a = str(when or "")[:10]
    b = str(as_of or "")[:10]
    return bool(a and b and a > b)


def predicate_terms(predicate: Any) -> set:
    """The words of a predicate's local name (``ex:annual_budget`` -> {annual, budget}; ``schema:worksFor`` ->
    {works, for}): used only to RANK which existing statements a packet shows first, never to decide that two
    statements are the same fact (the model names that by index, ADR 0004)."""
    raw = str(predicate or "")
    local = raw.split(":", 1)[1] if ":" in raw else raw
    out = []
    word = ""
    for ch in local:
        if ch.isalnum() and not (ch.isupper() and word and word[-1].islower()):
            word += ch.lower()
        else:
            if word:
                out.append(word)
            word = ch.lower() if ch.isalnum() else ""
    if word:
        out.append(word)
    return set(w for w in out if len(w) > 2)


def slot_first(claims: List[Any], prefer: Any, limit: int) -> List[Any]:
    """The statements a packet shows of one entity: EVERY one whose predicate the note itself uses (the note's
    slots: never cut, retro-design-review 2.1), then the others, those whose predicate shares a word with the
    note's first, up to ``limit`` in all; each group keeps the order it was given in."""
    wanted = set(prefer or ())
    terms = set()
    for x in wanted:
        terms |= predicate_terms(x)
    own = [c for c in claims if c.get("predicate") in wanted]
    rest = [c for c in claims if c.get("predicate") not in wanted]
    near = [c for c in rest if terms & predicate_terms(c.get("predicate"))]
    far = [c for c in rest if not (terms & predicate_terms(c.get("predicate")))]
    return own + (near + far)[:max(0, int(limit) - len(own))]


def note_envelope() -> str:
    """The user message's head ``<runtime_metadata>`` envelope: the SAME bytes for every call.

    The runtime keeps an envelope that is already at the head of a message byte for byte and
    stamps a fresh per-second one only on a message without one; a per-call timestamp there would
    end the shared prefix after a few tokens. The note's own time and id go at the END of the
    message (``note_header``), where they cannot break the cached prefix."""
    return "<runtime_metadata>{\"time\": \"the note's own date is on the last line of this message\"}</runtime_metadata>"


def note_header(note: Dict[str, Any], part: int = 0, parts: int = 1) -> str:
    """The message's LAST line: which note this is (id, title, when it was written, its kind, where
    it came from). It identifies the note and dates it ("now" for the note); it is not content."""
    n = note if isinstance(note, dict) else {}
    bits = ["About this note (not part of its text): " + str(n.get("note_id") or "")]
    if str(n.get("title") or "").strip():
        bits.append("“" + clip(n.get("title"), 160) + "”")
    when = str(n.get("occurred_at") or "")[:16].replace("T", " ")
    if when and n.get("date_approximate"):
        # A stand-in date (a file date, a commit, a session start): the text may state the real one.
        bits.append("written about " + when + " (now, for this note; this date is approximate, from the "
                    + str(n.get("date_basis") or "import").replace("_", " ") + ")")
    elif when:
        bits.append("written " + when + " (now, for this note)")
    extra = []
    if n.get("kind"):
        extra.append("kind " + str(n.get("kind")))
    src = n.get("source") if isinstance(n.get("source"), dict) else {}
    origin = [str(src.get(k)) for k in ("importer", "client", "author") if src.get(k)]
    if origin:
        extra.append("from " + "/".join(origin))
    if n.get("location"):
        extra.append("at " + clip(n.get("location"), 60))
    if extra:
        bits.append("(" + "; ".join(extra) + ")")
    if parts and int(parts) > 1:
        bits.append("[part " + str(int(part) + 1) + " of " + str(int(parts)) + "]")
    return " ".join(bits)


def extraction_packet(note: Dict[str, Any], section: Dict[str, Any], parts: int,
                      similar_notes: List[Dict[str, Any]], candidate_entities: List[Dict[str, Any]],
                      open_tasks: List[Dict[str, Any]], topics: List[Any]) -> Dict[str, str]:
    """The dynamic half of an extraction prompt: ``{envelope, header, known, text}``."""
    n = note if isinstance(note, dict) else {}
    sec = section if isinstance(section, dict) else {"text": str(section or "")}
    lines = []
    names = []
    for e in (candidate_entities or [])[:KNOWN_NAMES]:
        if isinstance(e, dict) and str(e.get("name") or "").strip():
            names.append(clip(e.get("name"), 80) + " — " + str(e.get("kind") or "thing"))
    if names:
        lines.append("Names already in the notebook that this text may use: " + "; ".join(names) + ".")
    counts = {}
    slots = {}
    order = []
    for e in (candidate_entities or [])[:KNOWN_NAMES]:
        if not isinstance(e, dict):
            continue
        for cl in (e.get("active_claims") or []) + (e.get("later_claims") or []):
            pred = str(cl.get("predicate") or "").strip() if isinstance(cl, dict) else ""
            if not pred or pred == "schema:mentions":  # the prompt never asks for a mentions statement
                continue
            counts[pred] = counts.get(pred, 0) + 1
            key = (str(cl.get("subject") or ""), pred, bool(cl.get("later")))
            if key not in slots:
                order.append(key)
                slots[key] = cl
            elif str(cl.get("observed_at") or "") > str(slots[key].get("observed_at") or ""):
                slots[key] = cl  # the latest value of the slot
    if counts:
        preds = [k for neg, k in sorted([(-n, k) for k, n in counts.items()])][:KNOWN_PREDICATES]
        lines.append("Predicates the notebook already uses about these names: " + ", ".join(preds) + ".")
    if order:
        shown = []
        for key in order[:KNOWN_SLOTS]:
            cl = slots[key]
            obj = cl.get("object")
            obj = ("“" + clip(obj, 60) + "”") if cl.get("object_is_literal") else clip(obj, 60)
            when = str(cl.get("observed_at") or "")[:10]
            tail = (when + ", recorded later than this note") if key[2] else when
            shown.append("- " + clip(key[0], 60) + " " + key[1] + " " + obj + (" (" + tail + ")" if tail else ""))
        if len(order) > KNOWN_SLOTS:
            shown.append("(" + str(len(order) - KNOWN_SLOTS) + " more statements not shown)")
        lines.append("Statements the notebook already has about these names (write the same kind of fact with the"
                     " same predicate):\n" + "\n".join(shown))
    own = [str(t) for t in (n.get("topics") or []) if str(t).strip()]
    rest = [str(t) for t in (topics or []) if str(t).strip() and str(t) not in own]
    shown = (own + rest)[:TOPICS]
    if shown:
        lines.append("Existing topics: " + ", ".join(shown) + ".")
    sims = []
    for s in (similar_notes or [])[:SIMILAR_NOTES]:
        if isinstance(s, dict):
            sims.append("“" + clip(s.get("title") or s.get("summary") or "untitled", 100) + "” ("
                        + str(s.get("occurred_at") or "")[:10] + ")")
    if sims:
        lines.append("Similar earlier notes: " + "; ".join(sims) + ".")
    tasks = []
    for t in (open_tasks or [])[:OPEN_TASKS]:
        if isinstance(t, dict) and str(t.get("text") or "").strip():
            tasks.append("- " + clip(t.get("text"), 140) + (" (due " + str(t.get("due"))[:10] + ")" if t.get("due") else ""))
    if tasks:
        lines.append("Open tasks:\n" + "\n".join(tasks))
    src = str(sec.get("source") or "note")
    who = str(sec.get("author") or "").strip()
    if src == "answer":
        label = "Answer by " + (who or "an assistant") + " (what they said; not written by the note's author)"
    elif src == "note":
        label = "Text written by " + who if who else "Text"
    else:
        label = "Text (" + src.replace("attachment:", "attached file ") + ")"
    try:
        part = int(sec.get("index") or 0)
    except (TypeError, ValueError):
        part = 0
    return {
        "envelope": note_envelope(),
        "header": note_header(n, part, parts),
        "known": "\n".join(lines) if lines else "The notebook knows nothing related yet.",
        "text": label + ":\n" + str(sec.get("text") or "").rstrip(),
    }


def claim_line(c: Dict[str, Any], now: Any = "") -> str:
    """An existing claim as one line: ``#index subject predicate object (since date)``, or ``(from date to date)``
    for a closed window. A claim first seen on a later day than the note's own date (``now``) that is shown
    outside the LATER block says ``LATER than this note``."""
    obj = c.get("object")
    obj = ("“" + clip(obj, 80) + "”") if c.get("object_is_literal") else clip(obj, 80)
    text = str(c.get("line") or "").strip() or (clip(c.get("subject"), 60) + " " + str(c.get("predicate") or "") + " " + obj)
    when = str(c.get("observed_at") or "")[:10]
    start = str(c.get("valid_from") or "")[:10] or when
    until = str(c.get("valid_until") or "")[:10]
    suffix = ""
    if until and start:
        suffix = " (from " + start + " to " + until + ")"
    elif until:
        suffix = " (until " + until + ")"
    elif when and later_than(when, now) and start and not later_than(start, now):
        suffix = " (stated later, said to hold since " + start + ")"  # known then by what a later note says
    elif when:
        suffix = " (since " + start + (", LATER than this note" if later_than(when, now) else "") + ")"
    return "#" + str(c.get("index")) + " " + text + suffix


def candidate_card(c: Dict[str, Any], prefer: Any = (), now: Any = "") -> List[str]:
    """An existing entity as the linking sees it on the note's date (``now``, ADR 0008 §2): its name, kind and
    aliases, its summary as known then, at most CLAIMS_PER_CANDIDATE statements believed then (those with a
    predicate the note also uses first), then a labelled LATER block with what was recorded after the note's
    date: every later statement within LATER_CLAIMS_PER_CANDIDATE (indexed, so a statement can be said to be an
    earlier state of one) and the latest later summaries; what the budget left out is said in a line."""
    aliases = [clip(a, 40) for a in (c.get("aliases") or []) if str(a).strip()][:5]
    head = "[" + str(c.get("index")) + "] " + clip(c.get("name"), 80) + " — " + str(c.get("kind") or "thing")
    head += ("; also " + ", ".join(aliases)) if aliases else ""
    day = str(now or "")[:10]
    later_summaries = [x for x in (c.get("later_summaries") or []) if isinstance(x, dict)]
    later_claims = [x for x in (c.get("later_claims") or []) if isinstance(x, dict)]
    if str(c.get("summary") or "").strip():
        written = str(c.get("summary_at") or "")[:10]
        head += " — " + ("[LATER] " if later_than(written, day) else "") + clip(c.get("summary"), SUMMARY_CHARS)
    elif later_summaries or later_claims:
        head += " — nothing was recorded about it by this note's date"
    if c.get("last_seen") and not later_than(c.get("last_seen"), day):
        head += " Last seen " + str(c.get("last_seen"))[:10] + "."
    out = [head]
    claims = [cl for cl in (c.get("active_claims") or []) if isinstance(cl, dict)]
    shown = slot_first(claims, prefer, CLAIMS_PER_CANDIDATE)
    for cl in shown:
        out.append("    " + claim_line(cl, now))
    left = int(c.get("known_claims_omitted") or 0) + len(claims) - len(shown)
    if left:
        out.append("    (+" + str(left) + " more not shown)")
    if later_claims or later_summaries:
        out.append("    " + LATER_LABEL)
        shown_later = slot_first(later_claims, prefer, LATER_CLAIMS_PER_CANDIDATE)
        for cl in [x for x in later_claims if x in shown_later]:  # in date order
            out.append("      " + claim_line(cl))
        for x in later_summaries[-LATER_SUMMARIES_PER_CANDIDATE:]:
            out.append("      summary as of " + str(x.get("at") or "")[:10] + ": " + clip(x.get("summary"), SUMMARY_CHARS))
        left_c = int(c.get("later_claims_omitted") or 0) + len(later_claims) - len(shown_later)
        left_s = int(c.get("later_summaries_omitted") or 0) + max(0, len(later_summaries) - LATER_SUMMARIES_PER_CANDIDATE)
        if left_c or left_s:
            out.append("      (+" + str(left_c) + " later statements and +" + str(left_s) + " later summaries not shown)")
    return out


def link_batches(note: Dict[str, Any], extraction: Dict[str, Any], resolved: List[Dict[str, Any]],
                 open_tasks: List[Dict[str, Any]], per_call: int = LINK_MENTIONS_PER_CALL) -> List[Dict[str, str]]:
    """The dynamic halves of the linking prompts for one note, one per call.

    Mentions are decided ``per_call`` at a time; each call carries the claims whose subject is
    one of its mentions (the first call also carries the claims about no mention, the tasks and
    the open tasks they may close). Claim and task numbers stay those of the whole extraction,
    and existing claims and open tasks keep their packet index, so the calls' answers merge
    without renumbering (``merge_linkings``)."""
    n = note if isinstance(note, dict) else {}
    ext = extraction if isinstance(extraction, dict) else {}
    mentions = [m for m in (ext.get("mentions") or []) if isinstance(m, dict)]
    claims = [c for c in (ext.get("claims") or []) if isinstance(c, dict)]
    tasks = [t for t in (ext.get("tasks") or []) if isinstance(t, dict)]
    names = {str(m.get("ref")): clip(m.get("name"), 60) for m in mentions}
    used = {}
    for c in claims:
        for end in (c.get("subject"), c.get("object")):
            used.setdefault(str(end), set()).add(c.get("predicate"))
    cands = {}
    for r in resolved or []:
        if isinstance(r, dict):
            cands[str(r.get("ref"))] = [c for c in (r.get("candidates") or []) if isinstance(c, dict)]
    size = max(1, int(per_call or LINK_MENTIONS_PER_CALL))
    groups = [mentions[i:i + size] for i in range(0, len(mentions), size)]
    if not groups and (claims or tasks):
        groups = [[]]  # statements or tasks about no mention still get one call; nothing at all gets none
    owner = {}
    for gi, group in enumerate(groups):
        for m in group:
            owner.setdefault(str(m.get("ref")), gi)
    by_group = [[] for g in groups]
    for i, c in enumerate(claims):
        by_group[owner.get(str(c.get("subject")), 0)].append((i, c))
    envelope = note_envelope()
    summary = clip(ext.get("summary"), 600)
    out = []
    for gi, group in enumerate(groups):
        mlines = []
        for m in group:
            ref = str(m.get("ref"))
            aliases = [clip(a, 40) for a in (m.get("aliases") or []) if str(a).strip()][:5]
            line = ref + " " + clip(m.get("name"), 80) + " — " + str(m.get("kind") or "thing")
            line += ("; also " + ", ".join(aliases)) if aliases else ""
            if str(m.get("role") or "").strip():
                line += ": " + clip(m.get("role"), 160)
            mlines.append(line)
            found = cands.get(ref) or []
            if not found:
                mlines.append("    no existing entity matched")
            for c in found[:CANDIDATES_PER_MENTION]:
                mlines.extend("    " + x for x in candidate_card(c, used.get(ref) or (), n.get("occurred_at")))
        clines = []
        for i, c in by_group[gi]:
            subj = str(c.get("subject"))
            obj = str(c.get("object") or "")
            if c.get("object_is_literal"):
                obj_text = "“" + clip(obj, 80) + "”"
            else:
                obj_text = obj + (" " + names[obj] if obj in names else "")
            line = "[" + str(i) + "] " + subj + (" " + names[subj] if subj in names else "") + " " + str(c.get("predicate") or "")
            line += " " + obj_text + " · " + str(c.get("certainty") or "stated")
            if str(c.get("stance") or "assert") != "assert":
                line += " · " + str(c.get("stance"))
            if str(c.get("evidence") or "").strip():
                line += " · “" + clip(c.get("evidence"), EVIDENCE_CHARS) + "”"
            clines.append(line)
        tlines = []
        if gi == 0:
            for i, t in enumerate(tasks):
                tlines.append("[" + str(i) + "] " + str(t.get("status") or "open") + ": " + clip(t.get("text"), 160)
                              + (" (due " + str(t.get("due"))[:10] + ")" if t.get("due") else ""))
        olines = []
        if gi == 0 and tasks:
            for t in (open_tasks or [])[:OPEN_TASKS]:
                if isinstance(t, dict):
                    olines.append("#" + str(t.get("index")) + " " + clip(t.get("text"), 140)
                                  + " (opened " + str(t.get("opened_at") or "")[:10] + ")")
        header = note_header(n)
        day = str(n.get("occurred_at") or "")[:10]
        if len(groups) > 1:
            header += " [linking part " + str(gi + 1) + " of " + str(len(groups)) + "]"
        out.append({
            "envelope": envelope,
            "header": header,
            "summary": summary or "(no summary)",
            "mentions": "\n".join(mlines) if mlines else "(none in this part)",
            "claims": "\n".join(clines) if clines else "(none in this part)",
            "tasks": "\n".join(tlines) if tlines else "(none)",
            "open_tasks": "\n".join(olines) if olines else "(none)",
            "as_of": ("Write every summary as of " + day + ", this note's date: what the entity was then, from this"
                      " note and what was known then; never use what was recorded later.") if day else
                     "Write every summary from this note and what was known when it was written.",
        })
    return out


def merge_linkings(parts: List[Any]) -> Dict[str, Any]:
    """One Linking from the answers of a note's linking calls (first decision per mention,
    claim and task wins; the revision summaries are joined)."""
    mentions, claims, tasks, summaries = [], [], [], []
    seen_m, seen_c, seen_t = set(), set(), set()
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        for m in p.get("mentions") or []:
            if isinstance(m, dict) and str(m.get("ref")) not in seen_m:
                seen_m.add(str(m.get("ref")))
                mentions.append(m)
        for c in p.get("claims") or []:
            if isinstance(c, dict) and c.get("index") not in seen_c:
                seen_c.add(c.get("index"))
                claims.append(c)
        for t in p.get("tasks") or []:
            if isinstance(t, dict) and t.get("index") not in seen_t:
                seen_t.add(t.get("index"))
                tasks.append(t)
        s = " ".join(str(p.get("revision_summary") or "").split())
        if s and s not in summaries:
            summaries.append(s)
    return {"mentions": mentions, "claims": claims, "tasks": tasks, "revision_summary": " ".join(summaries)}


def filing_packet(note_id: Any, occurred_at: Any, title: Any, text: Any, similar: List[Any], revisits: List[Any],
                  topics: List[Any]) -> Dict[str, str]:
    """The dynamic half of the quick filing prompt: ``{envelope, title, text, context, header}``."""
    lines = []
    sims = []
    for s in (similar or [])[:SIMILAR_NOTES]:
        if isinstance(s, dict) and (s.get("title") or s.get("note_id")):
            sims.append("“" + clip(s.get("title") or "untitled", 100) + "” (" + str(s.get("occurred_at") or "")[:10] + ")")
    if sims:
        lines.append("Similar earlier notes: " + "; ".join(sims) + ".")
    days = [str(r.get("occurred_at") or "")[:10] for r in (revisits or []) if isinstance(r, dict) and r.get("occurred_at")]
    if days:
        lines.append("Earlier notes with the same words: " + ", ".join(days[:5]) + ".")
    shown = [str(t) for t in (topics or []) if str(t).strip()][:TOPICS]
    if shown:
        lines.append("Existing topics: " + ", ".join(shown) + ".")
    when = str(occurred_at or "")[:16].replace("T", " ")
    return {
        "envelope": note_envelope(),
        "header": "About this note (not part of its text): " + str(note_id or "")
                  + (", written " + when + " (now, for this note)" if when else "") + ".",
        "title": clip(title, 200) if str(title or "").strip() else "(none)",
        "text": str(text or "").rstrip(),
        "context": "\n".join(lines) if lines else "Nothing related is saved yet.",
    }
