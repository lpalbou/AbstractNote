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


def claim_line(c: Dict[str, Any]) -> str:
    """An existing claim as one line: ``#index subject predicate object (since date)``."""
    obj = c.get("object")
    obj = ("“" + clip(obj, 80) + "”") if c.get("object_is_literal") else clip(obj, 80)
    text = str(c.get("line") or "").strip() or (clip(c.get("subject"), 60) + " " + str(c.get("predicate") or "") + " " + obj)
    when = str(c.get("observed_at") or "")[:10]
    return "#" + str(c.get("index")) + " " + text + (" (since " + when + ")" if when else "")


def candidate_card(c: Dict[str, Any], prefer: Any = ()) -> List[str]:
    """An existing entity as at most 1 + CLAIMS_PER_CANDIDATE lines: its current statements with a
    predicate the note also uses first (the ones a supersession or contradiction can be about),
    then the most recent."""
    aliases = [clip(a, 40) for a in (c.get("aliases") or []) if str(a).strip()][:5]
    head = "[" + str(c.get("index")) + "] " + clip(c.get("name"), 80) + " — " + str(c.get("kind") or "thing")
    head += ("; also " + ", ".join(aliases)) if aliases else ""
    if str(c.get("summary") or "").strip():
        head += " — " + clip(c.get("summary"), SUMMARY_CHARS)
    if c.get("last_seen"):
        head += " Last seen " + str(c.get("last_seen"))[:10] + "."
    out = [head]
    claims = [cl for cl in (c.get("active_claims") or []) if isinstance(cl, dict)]
    wanted = set(prefer or ())
    ranked = [cl for cl in claims if cl.get("predicate") in wanted] + [cl for cl in claims if cl.get("predicate") not in wanted]
    for cl in ranked[:CLAIMS_PER_CANDIDATE]:
        out.append("    " + claim_line(cl))
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
                mlines.extend("    " + x for x in candidate_card(c, used.get(ref) or ()))
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
