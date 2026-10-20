"""Turn an Extraction and a Linking into revision ops (docs/cognition.md, "Link" and "Apply").

Every reference the model makes is an index into the stored context packet;
anything outside it is rejected by set membership into ``create`` (mentions)
or ``new`` (claims) with a warning. Mapping:

- mention ``reuse`` -> the candidate entity, plus ``entity.alias`` for new names
  and ``entity.summary`` when the model rewrote the summary;
- ``create`` -> ``entity.create`` (id minted from the chosen name and kind);
- ``merge`` -> ``entity.merge`` (second candidate into the first) and reuse, only when
  the two share a name or alias key, have the same kind, the merged entry has fewer
  than :data:`MERGE_REVIEW_NOTES` notes and neither was merged earlier in the note;
  otherwise a ``skos:closeMatch`` claim for review (eval2 D1);
- ``unsure`` -> ``entity.create`` plus a ``skos:closeMatch`` claim to the candidate;
- a mention named by a number, a version, a message id, a link, a token or one generic
  word (:mod:`abstractnote.process.demote`) is a literal, never an entity (eval2 D8);
- claim ``new``/``refines`` -> ``claim.add``; ``restates`` -> ``claim.reinforce``;
  ``supersedes`` -> ``claim.add`` + ``claim.supersede`` (``valid_until`` = the
  note's ``occurred_at``; ``claim.retract`` when the note's stance is retract);
  ``contradicts`` -> ``claim.add`` marked disputed against the existing claim;
  both only against a statement on the same subject and predicate (eval2 D2);
  an identical active statement (same subject, predicate and object after
  resolution) is always reinforced rather than duplicated;
- time (ADR 0008 §2): a note OLDER than the statement it supersedes (or contradicts, unless the
  model marked it disputed within :data:`DISPUTE_WINDOW`) never closes it: its
  statement is placed as the earlier belief (``claim.add`` + ``claim.insert_before``), and so is
  ``earlier_state_of`` (a statement the model says is an earlier state of a LATER one, same
  subject, the existing statement after the note); an older note's ``restates`` / ``refines``
  (or an identical statement) moves the belief's start back to the note's date
  (``claim.reinforce`` + ``claim.start_earlier``), never later; a statement linked to an existing
  one on the same subject under another predicate spelling adopts the existing predicate
  (``predicate_adopted``: the model's index decides, never the string);
  a document date stated in the text of a note whose date is approximate moves the
  note first (``note.redate``); no window ever ends before it starts;
- tasks: ``task.close`` of the open task named by index, only with the note's own
  words saying it is done and never from a note older than the task (eval2 D3),
  else ``task.open`` (and ``task.close`` when the note reports it done);
- topics -> ``topic.assign``; the note summary -> ``note.summary``;
- a mention created (or reused as another entity) although a candidate matched it by an exact name or
  alias key is kept apart with a ``skos:closeMatch`` review item to that candidate (ADR 0008 §6).

The built revision also carries ``context`` (the digest the note was understood against, ``seen``) and
``used`` (:func:`used_of` its ops: the entities, claims and tasks it resolved to): the dependency record
of ADR 0008 §3 (the invalidation keys on ``used``).
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import ids
from ..ingest.dates import CONTENT, date_basis, document_date, is_approximate, redate_sentence
from ..ingest.normalise import normalize_topic, text_norm
from ..store.names import strip_kind_suffix
from ..store.notes import _TakenIds
from . import schemas, validate
from .context import empty_packet, load_packet, words
from .packets import later_than
from .demote import literal_reason
from .merge import merge_extractions
from .vocabulary import normalize_predicate

CLOSE_MATCH = "skos:closeMatch"
#: A model merge of an entry with at least this many notes goes to review instead (eval2 D1).
MERGE_REVIEW_NOTES = 5
#: A ``contradicts`` from an older note stays a dispute only when the model marked it disputed and the two
#: statements are at most this far apart; otherwise the older one is the earlier belief (data-model.md "Time").
DISPUTE_WINDOW = timedelta(days=30)
#: A stated document date moves the note only when it differs from the stand-in date by more than this.
REDATE_MIN = timedelta(days=1)


def _op(op_name: str, **payload: Any) -> Dict[str, Any]:
    return {"op": op_name, "payload": payload}


class _Builder:
    def __init__(self, nb: Any, note: Any, packet: Dict[str, Any]) -> None:
        self.nb = nb
        self.note = note
        self.note_id = note["note_id"]
        self.at = note["occurred_at"]
        self.packet = packet
        self.ops: List[Dict[str, Any]] = []
        self.warnings: List[str] = []
        # statements the linking named that are no longer believed on the note's date (ADR 0008 §5, review 4.1)
        self.stale_context: List[str] = []
        self.taken = _TakenIds(nb.store)
        self.refmap: Dict[str, str] = {}
        self.claim_ids: set = set()
        self.task_ids: set = set()
        self.mentioned: List[str] = []
        self.dropped: Dict[str, str] = {}
        self._note_words: Optional[str] = None
        self.stats = {"created": {}}
        self.merged: set = set()  # entities a merge of this revision already involves (no chains, eval2 D1)

    # ---------------------------------------------------------------- helpers
    def candidate(self, ref: str, index: Optional[int]) -> Optional[str]:
        cands = self.packet.get("mentions", {}).get(ref) or []
        if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < len(cands)):
            return None
        eid = self.nb.store.canonical(cands[index])
        return eid if self.nb.store.entity_row(eid) is not None else None

    def existing_claim(self, index: Optional[int]) -> Optional[str]:
        lst = self.packet.get("existing_claims") or []
        if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < len(lst)):
            return None
        row = self.nb.store.claim_row(lst[index])
        return row["claim_id"] if row is not None else None

    def open_task(self, index: Optional[int]) -> Optional[str]:
        lst = self.packet.get("open_tasks") or []
        if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < len(lst)):
            return None
        row = self.nb.store.q1("SELECT task_id FROM tasks WHERE task_id=? AND status='open'", (lst[index],))
        return row[0] if row else None

    def mention(self, eid: str) -> None:
        if eid not in self.mentioned:
            self.mentioned.append(eid)
            self.ops.append(_op("entity.mention", entity_id=eid, note_id=self.note_id))

    def known_names(self, eid: str) -> set:
        row = self.nb.store.entity_row(eid)
        names = {ids.fold(row["name"]), ids.fold(row["display_name"] or row["name"])} if row is not None else set()
        names |= {ids.fold(a) for a in self.nb.store.aliases(eid)}
        for op in self.ops:  # names added earlier in this revision
            p = op["payload"]
            if op["op"] == "entity.alias" and p.get("entity_id") == eid:
                names.add(ids.fold(p["alias"]))
            if op["op"] == "entity.create" and p.get("entity_id") == eid:
                names.add(ids.fold(p["name"]))
                names |= {ids.fold(a) for a in p.get("aliases") or []}
        return names

    def create(self, kind: str, name: str, aliases: Sequence[str], summary: Optional[str]) -> str:
        if kind not in ids.ENTITY_KINDS:
            self.warnings.append(f"The kind {kind!r} is not known; {name} was filed as a thing.")
            kind = "thing"
        eid = ids.mint_entity_id(kind, name, self.taken)
        self.taken.extra.add(eid)
        clean: List[str] = []
        seen = {ids.fold(name)}
        for a in aliases:
            a = str(a or "").strip()
            if a and ids.fold(a) not in seen:
                seen.add(ids.fold(a))
                clean.append(a)
        self.ops.append(_op("entity.create", entity_id=eid, kind=kind, name=name, aliases=clean,
                            summary=(summary or None)))
        return eid

    # ---------------------------------------------------------------- guards (eval2 D1)
    def name_keys(self, eid: str) -> set:
        """Canonical name keys of an entity and the entries merged into it: its name and aliases."""
        keys: set = set()
        for m in self.nb.store.merged_members(self.nb.store.canonical(eid)):
            row = self.nb.store.entity_row(m)
            if row is None:
                continue
            keys.update(k for k in (row["name_key"], ids.name_key(row["display_name"] or row["name"])) if k)
            keys.update(r[0] for r in self.nb.store.q("SELECT alias_key FROM entity_aliases WHERE entity_id=? AND"
                                                      " alias_key IS NOT NULL", (m,)))
        return keys

    def merge_refusal(self, into_id: str, from_id: str) -> Optional[str]:
        """Why a model merge of ``from_id`` into ``into_id`` is not applied (None: apply it). Structural only: the
        kinds, a shared name or alias key, the size of the merged entry, one merge per entry per note."""
        a, b = self.nb.store.entity_row(into_id), self.nb.store.entity_row(from_id)
        if {into_id, from_id} & self.merged:
            return "one merge per entry in a note"
        if a["kind"] != b["kind"]:
            return "their kinds differ"
        if not (self.name_keys(into_id) & self.name_keys(from_id)):
            return "they share no name"
        if int(b["note_count"] or 0) >= MERGE_REVIEW_NOTES:
            return f"{b['display_name'] or b['name']} has {int(b['note_count'])} notes"
        return None

    def review_merge(self, into_id: str, from_id: str, name: str, why: str) -> None:
        """A merge kept for review: a ``skos:closeMatch`` statement from the entry to merge to the one it would join
        (the review list shows it; confirming it merges them)."""
        cid = ids.claim_id(self.note_id, from_id, CLOSE_MATCH, into_id)
        if cid not in self.claim_ids:
            self.claim_ids.add(cid)
            self.ops.append(_op("claim.add", claim_id=cid, subject_id=from_id, predicate=CLOSE_MATCH,
                                object_id=into_id, object_literal=None, certainty="possible", stance="assert",
                                observed_at=self.at, valid_from=self.at, valid_until=None, evidence=name,
                                note_id=self.note_id))
        self.warnings.append(f"{name}: the two entries were not merged ({why}); the match is kept for review.")

    # ---------------------------------------------------------------- the note's own date
    def phrase_in_note(self, text: str) -> bool:
        ws = words(str(text or ""))
        return bool(ws) and (" " + " ".join(ws) + " ") in self.note_words()

    def redate(self, ext: schemas.Extraction) -> None:
        """``note.redate`` when the note's date is a stand-in and its text states its own date (the quote must be
        the note's words); the revision, its statements and its mentions then carry the stated date."""
        dd = ext.document_date
        if dd is None:
            return
        basis = date_basis(self.note)
        if not is_approximate(basis):
            if basis != CONTENT:
                self.warnings.append("The note's date is not approximate; the date its text states was not used.")
            return
        try:
            to, stated = document_date(dd.iso)  # a month or a year alone stays approximate (stated_month/_year)
        except ValueError:
            self.warnings.append(f"The document date {dd.iso!r} is not a date; it was ignored.")
            return
        if not self.phrase_in_note(dd.quote):
            self.warnings.append("The document date is not quoted from the note's words; it was ignored.")
            return
        if to > ids.utc_now():
            self.warnings.append("The document date is in the future; it was ignored.")
            return
        if abs(ids.parse_time(to) - ids.parse_time(self.at)) <= REDATE_MIN:
            return
        self.ops.append(_op("note.redate", note_id=self.note_id, **{"from": self.at}, to=to, basis=stated,
                            quote=dd.quote.strip()))
        self.at = to

    # ---------------------------------------------------------------- mentions
    def note_words(self) -> str:
        """The note's own words (text and extracted attachment text, not its title or header), as
        `` w1 w2 ... `` for whole-word sequence checks."""
        if getattr(self, "_note_words", None) is None:
            parts = [self.note["text"] or ""]
            for a in self.nb.store.attachments_of(self.note_id):
                path = self.nb.root / "extracted" / f"{a['sha256']}.txt"
                if a["extracted_chars"] and path.exists():
                    parts.append(path.read_text(encoding="utf-8"))
            self._note_words = " " + " ".join(words("\n".join(parts))) + " "
        return self._note_words

    def without_kind_suffix(self, m: Any) -> Any:
        """The mention (or mention decision) with :func:`strip_kind_suffix` applied to its name and aliases."""
        name = strip_kind_suffix(m.name) if m.name else m.name
        aliases = [strip_kind_suffix(a) for a in m.aliases]
        if name == m.name and aliases == list(m.aliases):
            return m
        if name != m.name:
            self.warnings.append(f"“{m.name}” ends with its kind in parentheses; it is named {name}.")
        return m.model_copy(update={"name": name, "aliases": aliases})

    def written_in_note(self, m: schemas.Mention) -> bool:
        """Structural check (backlog 0024): the mention's name or one of its aliases occurs in the
        note's own words, as a whole-word sequence (case-folded). Names from the context packet or
        the header that the text never writes are refused."""
        for name in [m.name, *m.aliases]:
            ws = words(str(name or ""))
            if ws and (" " + " ".join(ws) + " ") in self.note_words():
                return True
        return False

    def mentions(self, ext: schemas.Extraction, link: schemas.Linking) -> None:
        by_ref: Dict[str, schemas.MentionLink] = {}
        for lm in link.mentions:
            by_ref.setdefault(lm.ref, lm)
        known_refs = {m.ref for m in ext.mentions}
        for lm in link.mentions:
            if lm.ref not in known_refs:
                self.warnings.append(f"The linking named a mention {lm.ref} that the extraction does not have.")
        for m in ext.mentions:
            m = self.without_kind_suffix(m)
            if m.ref in self.refmap or m.ref in self.dropped:
                self.warnings.append(f"The mention ref {m.ref} appears twice; the first one was used.")
                continue
            if not self.written_in_note(m):
                self.dropped[m.ref] = m.name
                self.warnings.append(f"{m.name} is not written in the note; it was left out.")
                continue
            why = literal_reason(m.name)
            if why:  # a value, not a thing (eval2 D8): its statements keep it as text
                self.dropped[m.ref] = m.name
                self.warnings.append(f"“{m.name}” is {why}, not an entry; it was kept as text.")
                continue
            lm = by_ref.get(m.ref)
            if lm is not None:
                lm = self.without_kind_suffix(lm)
            if lm is None:
                self.warnings.append(f"No linking decision for {m.name} ({m.ref}); it was created as new.")
            decision = lm.decision if lm else "create"
            eid: Optional[str] = None
            close_to: Optional[str] = None
            if decision == "reuse":
                eid = self.candidate(m.ref, lm.candidate)
                if eid is None:
                    self.warnings.append(f"{m.name}: the chosen candidate is not in its list; it was created as new.")
            elif decision == "merge":
                mc = list(lm.merge_candidates or [])
                a = self.candidate(m.ref, mc[0]) if len(mc) == 2 else None
                b = self.candidate(m.ref, mc[1]) if len(mc) == 2 else None
                if a and b and a != b:
                    refused = self.merge_refusal(a, b)
                    if refused is None:
                        self.ops.append(_op("entity.merge", from_id=b, into_id=a, reason=lm.reason or "same entity"))
                        self.merged.update({a, b})
                    else:
                        self.review_merge(a, b, m.name, refused)
                    eid = a
                else:
                    eid = self.candidate(m.ref, lm.candidate)
                    self.warnings.append(f"{m.name}: the merge did not name two different candidates from its list; "
                                         + ("the candidate was reused." if eid else "it was created as new."))
            elif decision == "unsure":
                close_to = self.candidate(m.ref, lm.candidate)
                if close_to is None:
                    self.warnings.append(f"{m.name} was marked unsure without a candidate; it was created as new.")
            name = (lm.name.strip() if lm and lm.name and lm.name.strip() else m.name.strip()) or m.ref
            if eid is None:
                kind = (lm.kind if lm and lm.kind else m.kind)
                summary = (lm.summary or "").strip() if lm else ""
                if not summary:
                    summary = placeholder_summary(m.role, self.note)
                    self.warnings.append(f"No summary was written for {name}; it was described from this note.")
                eid = self.create(kind, name, [m.name, *m.aliases, *((lm.aliases if lm else []) or [])], summary)
                self.stats["created"][eid] = m.ref
                if close_to:
                    self.close_match(eid, close_to, m.name)
            else:
                # A reused entity gains only the aliases the linking lists for it (backlog 0024):
                # the mention's own spelling may name a part or a namesake, not the entity.
                known = self.known_names(eid)
                for alias in ((lm.aliases if lm else []) or []):
                    alias = str(alias or "").strip()
                    if alias and ids.fold(alias) not in known:
                        known.add(ids.fold(alias))
                        self.ops.append(_op("entity.alias", entity_id=eid, alias=alias))
                if lm and lm.summary and lm.summary.strip():
                    current = self.nb.store.entity_row(eid)["summary"]
                    if lm.summary.strip() != (current or "").strip():
                        self.ops.append(_op("entity.summary", entity_id=eid, summary=lm.summary.strip(),
                                            previous=current))
            self.refmap[m.ref] = eid
            self.mention(eid)
            self.alias_review(m, eid, decision)
        about = json.loads(self.note["about_json"]) if self.note["about_json"] else None
        if about and about.get("kind") == "entity" and self.nb.store.entity_row(about["id"]) is not None:
            self.mention(self.nb.store.canonical(about["id"]))

    def close_match(self, eid: str, other: str, evidence: str) -> bool:
        """A ``skos:closeMatch`` statement from ``eid`` to ``other`` (a review item); False when this revision
        already has it."""
        cid = ids.claim_id(self.note_id, eid, CLOSE_MATCH, other)
        if cid in self.claim_ids:
            return False
        self.claim_ids.add(cid)
        self.ops.append(_op("claim.add", claim_id=cid, subject_id=eid, predicate=CLOSE_MATCH, object_id=other,
                            object_literal=None, certainty="possible", stance="assert", observed_at=self.at,
                            valid_from=self.at, valid_until=None, evidence=evidence, note_id=self.note_id))
        return True

    def alias_matches(self, ref: str) -> List[str]:
        """The candidates that matched mention ``ref`` by an exact name or alias key (the packet's)."""
        return list((self.packet.get("alias_matches") or {}).get(ref) or [])

    def alias_review(self, m: Any, eid: str, decision: str) -> None:
        """ADR 0008 §6: a candidate that matched the mention by an exact name or alias key and that the decision did
        not make this entity (created apart, or another candidate reused) is kept as a close match for review."""
        if decision == "merge":
            return  # the model related the two records itself (merged, or kept for review by the merge guard)
        paired = {frozenset((op["payload"]["subject_id"], op["payload"]["object_id"])) for op in self.ops
                  if op["op"] == "claim.add" and op["payload"].get("predicate") == CLOSE_MATCH}
        for other in self.alias_matches(m.ref):
            other = self.nb.store.canonical(other)
            if other == eid or self.nb.store.entity_row(other) is None or other in self.merged \
                    or frozenset((eid, other)) in paired:
                continue
            if self.close_match(eid, other, m.name):
                self.warnings.append(f"{m.name}: a name or alias matches {other} exactly, but it was "
                                     + ("linked to another entry" if decision == "reuse" and eid not in self.stats["created"]
                                        else "created apart")
                                     + "; the match is kept for review.")

    # ---------------------------------------------------------------- claims
    def identical_active(self, subject: str, predicate: str, obj_id: Optional[str], literal: Optional[str]
                         ) -> Optional[str]:
        """The statement already believed with the same subject, predicate and object (after resolution) whose
        window is still open on the note's date or starts after it, never retracted: the one covering the note's
        date first, else the earliest later one. None: the statement is new."""
        s = self.nb.store.merged_members(self.nb.store.canonical(subject))
        sql = (f"SELECT claim_id, valid_from FROM claims WHERE status!='retracted' AND (valid_until IS NULL OR"
               f" valid_until>?) AND predicate=? AND subject_id IN ({','.join('?' for _ in s)})")
        params: List[Any] = [self.at, predicate, *s]
        if obj_id:
            o = self.nb.store.merged_members(self.nb.store.canonical(obj_id))
            sql += f" AND object_id IN ({','.join('?' for _ in o)})"
            params += o
        else:
            sql += " AND object_id IS NULL AND object_literal=?"
            params.append(literal)
        rows = self.nb.store.q(sql + " ORDER BY observed_at, claim_id", params)
        covering = [r for r in rows if r[1] is None or r[1] <= self.at]
        return (covering or rows)[0][0] if rows else None

    def _time(self, value: Optional[str], what: str, i: int) -> Optional[str]:
        if value in (None, ""):
            return None
        try:
            return ids.normalize_time(value, field=what)
        except ValueError:
            self.warnings.append(f"Statement {i}: the {what} {value!r} is not a date; it was ignored.")
            return None

    def claims(self, ext: schemas.Extraction, link: schemas.Linking) -> None:
        by_index: Dict[int, schemas.ClaimLink] = {}
        for lc in link.claims:
            by_index.setdefault(lc.index, lc)
        self.extra_links("statement", "existing statements",
                         [(lc.index, lc.relation_to_existing, lc.existing) for lc in link.claims],
                         "only the link to #{first} ({relation}) was applied")
        for lc in link.claims:
            if not (0 <= lc.index < len(ext.claims)):
                self.warnings.append(f"The linking named statement {lc.index}, which the extraction does not have.")
        for i, c in enumerate(ext.claims):
            subject = self.refmap.get(c.subject)
            if subject is None:
                if c.subject not in self.dropped:
                    self.warnings.append(f"Statement {i}: its subject {c.subject!r} is not a mention; it was skipped.")
                continue
            obj_id: Optional[str] = None
            literal: Optional[str] = None
            if c.object_is_literal:
                literal = str(c.object)
            elif c.object in self.refmap:
                obj_id = self.refmap[c.object]
            elif c.object in self.dropped:
                literal = self.dropped[c.object]
            else:
                literal = str(c.object)
                self.warnings.append(f"Statement {i}: its object {c.object!r} is not a mention; it was kept as text.")
            if literal is not None and not literal.strip():
                self.warnings.append(f"Statement {i} has an empty object; it was skipped.")
                continue
            predicate, w = normalize_predicate(c.predicate)
            if w:
                self.warnings.append(w)
            lc = by_index.get(i)
            relation = lc.relation_to_existing if lc else "new"
            existing = None
            if lc is not None and lc.existing is not None:
                existing = self.existing_claim(lc.existing)
                if existing is None:
                    self.warnings.append(f"Statement {i} referred to an existing statement outside the context; "
                                         "it was treated as new.")
            if relation != "new" and existing is None:
                if lc is not None and lc.existing is None:
                    self.warnings.append(f"Statement {i} was marked {relation} without naming the existing statement;"
                                         " it was treated as new.")
                relation = "new"
            ex_row = self.nb.store.claim_row(existing) if existing else None
            if relation == "earlier_state_of":
                why = self.not_later(ex_row)
                if why:  # ADR 0008 §2: the model named a statement that is not LATER than this note
                    self.warnings.append(f"Statement {i} was marked an earlier state of a statement that {why}; it "
                                         "was treated as new.")
                    relation, existing, ex_row = "new", None, None
            elif relation == "supersedes" and ex_row is not None and self.later_in_lineage(ex_row):
                relation = "earlier_state_of"  # an older note never closes a later belief: it is placed before it
            elif relation == "contradicts" and ex_row is not None and self.at < ex_row["observed_at"] \
                    and not self.still_disputed(c.certainty, existing):
                relation = "earlier_state_of"  # an older note's view is the earlier belief, not a dispute
            if relation in ("supersedes", "contradicts", "earlier_state_of") and ex_row is not None:
                if not self.same_subject(ex_row, subject):
                    # eval2 D2: only a statement about the same subject can replace, dispute or follow it
                    self.warnings.append(f"Statement {i} was linked to an existing statement about another subject; "
                                         "it was kept as new.")
                    relation, existing, ex_row = "new", None, None
                elif ex_row["predicate"] != predicate and (ex_row["object_id"] is None) == (obj_id is None):
                    # The model's index says it is the same fact (ADR 0004, retro-design-review 2.6): the notebook
                    # keeps one predicate per fact, the existing one.
                    self.warnings.append(f"predicate_adopted: {predicate} -> {ex_row['predicate']} (statement {i} "
                                         "is the same fact as the existing statement it names).")
                    predicate = ex_row["predicate"]
                elif ex_row["predicate"] != predicate:
                    # a value of another kind (a text against an entity: "intern" before "leads Zorn Labs"): the
                    # statement keeps its predicate and is linked across the two (structural: object kinds only)
                    self.warnings.append(f"Statement {i} is linked to an existing statement under another predicate "
                                         f"({ex_row['predicate']}) with another kind of value; it keeps {predicate}.")
            cid = ids.claim_id(self.note_id, subject, predicate, obj_id or literal)
            if cid in self.claim_ids:
                continue
            given_from = self._time(c.valid_from, "valid_from", i)
            valid_from: Optional[str] = given_from or self.at
            valid_until = self._time(c.valid_until, "valid_until", i)
            if valid_until and valid_from and valid_until < valid_from:
                if given_from:  # never a negative window
                    self.warnings.append(f"Statement {i} ends before it starts; its end was ignored.")
                    valid_until = None
                else:  # it stopped before this note was written: when it started is not known
                    valid_from = None
            payload = dict(claim_id=cid, subject_id=subject, predicate=predicate, object_id=obj_id,
                           object_literal=literal, certainty=c.certainty, stance=c.stance, observed_at=self.at,
                           valid_from=valid_from, valid_until=valid_until, evidence=c.evidence or None,
                           note_id=self.note_id)
            reason = (lc.reason if lc and lc.reason else "")
            if relation == "restates":
                self.reinforce(existing, payload, i, reason)
                continue
            same = self.identical_active(subject, predicate, obj_id, literal)
            if same:
                # The very statement is already believed: reinforce it instead of duplicating it.
                self.reinforce(same, payload, i, reason)
                if relation == "supersedes" and existing != same:
                    if self.at >= self.nb.store.claim_row(existing)["observed_at"]:
                        self.ops.append(_op("claim.supersede", claim_id=existing, by_claim_id=same,
                                            reason=reason or "a later note changed it",
                                            valid_until=self.closing_at(existing)))
                    else:
                        self.warnings.append(f"Statement {i}: this note is older than the statement it would "
                                             "replace; that statement stays.")
                continue
            self.claim_ids.add(cid)
            if relation == "contradicts":
                payload["certainty"] = "disputed"
                payload["disputes"] = existing
                self.ops.append(_op("claim.add", **payload))
            elif relation == "earlier_state_of":
                self.insert_before(payload, existing, reason or "an earlier state, replaced later")
            elif relation == "supersedes" and not self.believed_on_note_date(ex_row):
                # nothing to close: the statement it names already ended by this note's date (or was retracted)
                self.warnings.append(f"Statement {i} would replace a statement that is no longer believed on this "
                                     "note's date; it was kept as new.")
                self.stale_context.append(existing)
                self.ops.append(_op("claim.add", **payload))
            elif relation == "supersedes":
                self.ops.append(_op("claim.add", **payload))
                reason = reason or "a later note changed it"
                if self.closing_at(existing) == ex_row["valid_from"]:
                    # B P1d: the replaced statement began at this very time; its window is empty, and that is said
                    self.warnings.append(f"Statement {i} replaces a statement that began at the same time (same day);"
                                         " the replaced one now holds an empty window.")
                if c.stance == "retract":
                    self.ops.append(_op("claim.retract", claim_id=existing, reason=reason,
                                        valid_until=self.closing_at(existing)))
                else:
                    self.ops.append(_op("claim.supersede", claim_id=existing, by_claim_id=cid, reason=reason,
                                        valid_until=self.closing_at(existing)))
            else:
                self.ops.append(_op("claim.add", **payload))
                if relation == "refines" and ex_row is not None and self.at < ex_row["observed_at"]:
                    # the earlier note shows the refined belief already held on its date
                    op = self.start_earlier_op(ex_row, payload, reason or "an earlier note refines it")
                    if op is not None:
                        self.ops.append(op)
                elif relation == "new":
                    self.parallel_belief(i, payload)

    def extra_links(self, what: str, targets: str, links: List[Tuple[int, str, Optional[int]]], applied: str) -> None:
        """ADR 0007: one statement (or task) linked to SEVERAL existing statements (open tasks) is a valid answer
        (validate.py), but the builder applies one link per index, the first; every other target is said in a
        ``multi_link`` warning, never dropped silently. Identical repeats are not counted."""
        seen: Dict[int, List[Tuple[str, Optional[int]]]] = {}
        for index, relation, target in links:
            if target is None:
                continue
            got = seen.setdefault(index, [])
            if (relation, target) not in got:
                got.append((relation, target))
        for index, got in seen.items():
            if len(got) < 2:
                continue
            first_rel, first = got[0]
            rest = ", ".join(f"#{t} ({r})" if r != first_rel else f"#{t}" for r, t in got[1:])
            self.warnings.append(f"multi_link: {what} {index} was linked to {len(got)} {targets}; "
                                 + applied.format(first=first, relation=first_rel)
                                 + f", the link{'s' if len(got) > 2 else ''} to {rest} {'were' if len(got) > 2 else 'was'}"
                                 " not.")

    # ---------------------------------------------------------------- reinforcement by an earlier note
    def reinforce(self, claim_id: str, payload: Dict[str, Any], i: int, reason: str) -> None:
        """``claim.reinforce`` of an existing belief; from a note OLDER than it, the belief's start moves back to
        the note's date (``claim.start_earlier``, never later). When an earlier state of that belief began after
        this note's date, the statement is instead placed as its own earlier belief before that state."""
        row = self.nb.store.claim_row(claim_id)
        if row is None or row["status"] == "retracted" or not (self.at < row["observed_at"]):
            self.ops.append(_op("claim.reinforce", claim_id=claim_id, note_id=self.note_id))
            return
        between = [r for r in self.lineage(row) if r["claim_id"] != row["claim_id"]
                   and self.at < r["observed_at"] < row["observed_at"]]
        if between:
            if payload["claim_id"] not in self.claim_ids:
                self.claim_ids.add(payload["claim_id"])
                self.warnings.append(f"Statement {i}: the belief it restates began later than this note, after "
                                     "another state; it was placed as its own earlier belief.")
                self.insert_before(payload, claim_id, reason or "the same value before a later state")
            return
        self.ops.append(_op("claim.reinforce", claim_id=claim_id, note_id=self.note_id))
        op = self.start_earlier_op(row, payload, reason or "an earlier note says it already held")
        if op is not None:
            self.ops.append(op)

    def start_earlier_op(self, row: Any, payload: Dict[str, Any], reason: str) -> Optional[Dict[str, Any]]:
        return start_earlier_op(self.nb.store, row, self.at, self.note_id, payload.get("evidence"), reason)

    def parallel_belief(self, i: int, payload: Dict[str, Any]) -> None:
        """A ``new`` statement on a subject and predicate whose LATER statements the packet showed (the S1 failure:
        a retro value beside the later ones): kept (the model decides; a relation that adds up is many values),
        and said in the revision (``parallel_belief``) so it is visible and countable, never silent."""
        shown = set(self.packet.get("existing_claims") or [])
        if not shown:
            return
        canon = self.nb.store.canonical
        members = self.nb.store.merged_members(canon(payload["subject_id"]))
        q = ",".join("?" for _ in members)
        rows = self.nb.store.q(f"SELECT claim_id, observed_at FROM claims WHERE status!='retracted' AND predicate=?"
                               f" AND subject_id IN ({q}) ORDER BY observed_at", [payload["predicate"], *members])
        later = [r[0] for r in rows if r[0] in shown and later_than(r[1], self.at)]
        if later:
            idx = [str((self.packet.get("existing_claims") or []).index(x)) for x in later]
            self.warnings.append(f"parallel_belief: statement {i} was kept as new beside the later statement"
                                 f"{'s' if len(idx) > 1 else ''} #{', #'.join(idx)} on the same subject and "
                                 f"predicate (the linking named no earlier state).")

    # ---------------------------------------------------------------- time-aware placement
    def same_subject(self, row: Any, subject: str) -> bool:
        canon = self.nb.store.canonical
        return row is not None and canon(row["subject_id"]) == canon(subject)

    def believed_on_note_date(self, row: Any) -> bool:
        return row is not None and row["status"] == "active" and (row["valid_until"] is None
                                                                  or row["valid_until"] > self.at)

    def later_in_lineage(self, row: Any) -> bool:
        """Whether the belief history ``row`` belongs to holds a statement first seen after this note's date."""
        return any(self.at < r["observed_at"] for r in self.lineage(row))

    def not_later(self, row: Any) -> Optional[str]:
        """Why ``row`` cannot be the LATER statement of an ``earlier_state_of`` (None: it can): it must be first seen
        after this note's date and not be said to hold already on it."""
        if row is None:
            return "is not in the context"
        if row["status"] == "retracted":
            return "was retracted"
        if not (self.at < row["observed_at"]):
            return "is not later than this note"
        if row["valid_from"] is not None and row["valid_from"] <= self.at:
            return "is said to hold already on this note's date"
        return None

    def closing_at(self, existing: str) -> str:
        """When this note closes an existing statement: its date, never before the statement's own start (an empty
        window, never a negative one; the same value reaches the note index and AbstractMemory)."""
        start = self.nb.store.claim_row(existing)["valid_from"]
        return start if start and start > self.at else self.at

    def still_disputed(self, certainty: str, existing: str) -> bool:
        """An older note's ``contradicts`` stays a dispute only when the model marked it disputed and the two
        statements are at most :data:`DISPUTE_WINDOW` apart."""
        t_old = self.nb.store.claim_row(existing)["observed_at"]
        return certainty == "disputed" and ids.parse_time(t_old) - ids.parse_time(self.at) <= DISPUTE_WINDOW

    def lineage(self, anchor: Any) -> List[Any]:
        return lineage_of(self.nb.store, anchor)

    def insert_before(self, payload: Dict[str, Any], existing: str, reason: str) -> None:
        """A statement from a note OLDER than the belief it relates to (data-model.md "Time"): it is placed in its
        interval of the belief's history as the earlier belief: ``valid_until`` = the start of the next later
        belief, superseded by it (never closing it), and the belief before it, if any, now ends where it starts.
        Ops: ``claim.add`` then ``claim.insert_before``."""
        rows = self.lineage(self.nb.store.claim_row(existing))
        key = lambda r: (r["observed_at"], r["seq"] or 0)  # noqa: E731
        later = sorted((r for r in rows if r["observed_at"] > self.at), key=key)
        succ = later[0]
        start = payload["valid_from"]
        end = succ["valid_from"] or succ["observed_at"]
        if start and end < start:
            end = start  # an empty window, never a negative one
        if payload["valid_until"] is None or payload["valid_until"] > end:
            payload["valid_until"] = None  # closed by claim.insert_before at ``end`` (close_assertion, older copy)
        self.ops.append(_op("claim.add", **payload))
        ib: Dict[str, Any] = dict(claim_id=payload["claim_id"], before_claim_id=succ["claim_id"], valid_until=end,
                                  reason=reason)
        cut = start or self.at
        prev = [r for r in rows if r["observed_at"] <= self.at and r["claim_id"] != succ["claim_id"]
                and (r["valid_until"] is None or r["valid_until"] > cut)]
        if prev:
            p = max(prev, key=key)
            p_start = p["valid_from"]
            ib.update(after_claim_id=p["claim_id"], after_valid_until=max(cut, p_start) if p_start else cut)
        self.ops.append(_op("claim.insert_before", **ib))

    # ---------------------------------------------------------------- tasks, topics, summary
    def tasks(self, ext: schemas.Extraction, link: schemas.Linking) -> None:
        by_index: Dict[int, schemas.TaskLink] = {}
        for lt in link.tasks:
            by_index.setdefault(lt.index, lt)
        self.extra_links("task", "open tasks", [(lt.index, "closes", lt.closes_open_task) for lt in link.tasks],
                         "only open task #{first} was considered for closing")
        closed: set = set()
        for i, t in enumerate(ext.tasks):
            if not t.text.strip():
                continue
            lt = by_index.get(i)
            target = None
            if lt is not None and lt.closes_open_task is not None:
                target = self.open_task(lt.closes_open_task)
                if target is None:
                    self.warnings.append(f"Task {i} named an open task outside the context; it was kept as a task of "
                                         "this note.")
            due = None
            if t.due:
                try:
                    due = ids.normalize_time(t.due, field="due date")
                except ValueError:
                    self.warnings.append(f"Task {i}: the due date {t.due!r} is not a date; it was ignored.")
            if target:
                target = self.checked_close(i, target, lt)
            if target and target not in closed:
                closed.add(target)
                self.ops.append(_op("task.close", task_id=target, by_note_id=self.note_id, text=t.text))
                continue
            tid = ids.task_id(self.note_id, text_norm(t.text))
            if tid in self.task_ids:
                continue
            self.task_ids.add(tid)
            self.ops.append(_op("task.open", task_id=tid, text=t.text, due=due, note_id=self.note_id))
            if t.status == "done":
                self.ops.append(_op("task.close", task_id=tid, by_note_id=self.note_id, text=t.text))

    def checked_close(self, i: int, target: str, lt: schemas.TaskLink) -> Optional[str]:
        """eval2 D3: an open task is closed only when the linking quotes the note's own words saying it is done and
        the note is not older than the task; otherwise the task stays open (this note's task is kept as its own)."""
        if not self.phrase_in_note(lt.evidence):
            self.warnings.append(f"Task {i}: closing an open task needs the note's words that say it is done; the "
                                 "open task stays open.")
            return None
        opened = self.nb.store.q1("SELECT opened_at FROM tasks WHERE task_id=?", (target,))
        if opened is not None and opened[0] and self.at < opened[0]:
            self.warnings.append(f"Task {i}: this note is older than the open task it would close; the open task "
                                 "stays open.")
            return None
        return target

    def topics(self, ext: schemas.Extraction) -> None:
        seen = set()
        for t in ext.topics:
            tt = normalize_topic(t)
            if tt and tt not in seen:
                seen.add(tt)
                self.ops.append(_op("topic.assign", note_id=self.note_id, topic=tt))


def lineage_of(store: Any, anchor: Any) -> List[Any]:
    """The beliefs one statement belongs to: the claims linked to it by supersession (both ways) on the same
    subject, retracted ones left out. Any predicate: a statement the model linked by index to one with another
    kind of value keeps its own predicate (``Builder.claims``), and that link is part of the history."""
    members = set(store.merged_members(store.canonical(anchor["subject_id"])))
    seen = {anchor["claim_id"]: anchor}
    todo = [anchor]
    while todo:
        x = todo.pop()
        linked = list(store.q("SELECT * FROM claims WHERE superseded_by=?", (x["claim_id"],)))
        if x["superseded_by"]:
            nxt = store.claim_row(x["superseded_by"])
            if nxt is not None:
                linked.append(nxt)
        for r in linked:
            if r["claim_id"] in seen or r["status"] == "retracted" or r["subject_id"] not in members:
                continue
            seen[r["claim_id"]] = r
            todo.append(r)
    return list(seen.values())


def start_earlier_op(store: Any, row: Any, at: str, note_id: Optional[str], evidence: Optional[str], reason: str
                     ) -> Optional[Dict[str, Any]]:
    """``claim.start_earlier``: the belief ``row`` was already held on ``at`` (an earlier note's date). Its
    ``observed_at`` becomes ``at`` and its ``valid_from`` the earlier of its own and ``at`` (never later; a statement
    without a known start keeps none); the belief before it in its lineage whose window covered ``at`` now ends
    there. The payload carries the statement's content for AbstractMemory, which records the earlier evidence as
    an additive assertion (data-model.md "AbstractMemory projection"). None when ``row`` is not later than ``at``."""
    if not (at < row["observed_at"]):
        return None
    if any(r["claim_id"] != row["claim_id"] and at < r["observed_at"] < row["observed_at"] for r in lineage_of(store, row)):
        return None  # an earlier state of it began after ``at``: moving its start back would overlap that state
    start = row["valid_from"]
    new_from = start if (start is None or start <= at) else at
    p: Dict[str, Any] = dict(
        claim_id=row["claim_id"], note_id=note_id, observed_at=at, valid_from=new_from,
        previous_observed_at=row["observed_at"], previous_valid_from=start, subject_id=row["subject_id"],
        predicate=row["predicate"], object_id=row["object_id"], object_literal=row["object_literal"],
        certainty=row["certainty"], stance=row["stance"], evidence=evidence, reason=reason)
    if new_from != start:
        prev = [r for r in lineage_of(store, row) if r["claim_id"] != row["claim_id"] and r["observed_at"] <= at
                and (r["valid_until"] is None or r["valid_until"] > at)]
        if prev:
            pr = max(prev, key=lambda r: (r["observed_at"], r["seq"] or 0))
            p.update(after_claim_id=pr["claim_id"], after_valid_until=max(at, pr["valid_from"]) if pr["valid_from"] else at)
    return _op("claim.start_earlier", **p)


def placeholder_summary(role: str, note: Any) -> str:
    """A summary for a created entity the model left without one: its role in the note, as a sentence,
    else where it was first seen. Never a failed note (backlog 0024)."""
    role = " ".join(str(role or "").split())
    if role:
        sentence = role[0].upper() + role[1:]
        return sentence if sentence[-1] in ".!?" else sentence + "."
    title = " ".join(str(note["title"] or "").split())
    day = str(note["occurred_at"] or "")[:10]
    where = f"\u201c{title}\u201d" if title else "a note"
    return f"Mentioned in {where}" + (f" on {day}." if day else ".")


def _note_missing_summaries(b: "_Builder", ext: schemas.Extraction, link: schemas.Linking) -> None:
    """A reused or merged entity that this note tells something new about should get a summary sentence;
    when the model left it out, the entity keeps its current summary and the revision says so."""
    changed: set = set()
    for op in b.ops:
        p = op["payload"]
        if op["op"] == "claim.add":
            changed.update(x for x in (p["subject_id"], p.get("object_id")) if x)
        elif op["op"] in ("claim.supersede", "claim.retract"):
            row = b.nb.store.claim_row(p["claim_id"])
            if row is not None:
                changed.update(x for x in (row["subject_id"], row["object_id"]) if x)
        elif op["op"] == "entity.merge":
            changed.add(p["into_id"])
    by_ref = {lm.ref: lm for lm in reversed(link.mentions)}
    missing = []
    for m in ext.mentions:
        lm = by_ref.get(m.ref)
        eid = b.refmap.get(m.ref)
        if lm is None or eid is None or lm.decision not in ("reuse", "merge") or eid in b.stats["created"]:
            continue
        if eid in changed and not (lm.summary or "").strip():
            missing.append(m.name)
    if missing:
        b.warnings.append("No new summary was written for " + ", ".join(missing)
                          + "; the earlier summary was kept.")


def used_of(ops: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    """What a revision USED (ADR 0008 §3-4, retro-design-review 3.1): the entities it mentioned, created,
    aliased, summarised, merged or made the subject or object of a statement; the existing statements it related
    to (reinforced, closed, placed before or after, moved back, disputed); the tasks it opened or closed. Read from
    the ops alone, so it is computed again from a stored (rewritten) revision. Invalidation keys on this, never
    on what the packet merely showed (``context``)."""
    ents: List[str] = []
    claims: List[str] = []
    tasks: List[str] = []
    added = set()

    def put(lst: List[str], x: Any) -> None:
        if x and x not in lst:
            lst.append(str(x))

    for op in ops or []:
        name, p = op.get("op"), op.get("payload") or {}
        if name in ("entity.mention", "entity.create", "entity.alias", "entity.summary", "entity.rename"):
            put(ents, p.get("entity_id"))
        elif name == "entity.merge":
            put(ents, p.get("into_id"))
            put(ents, p.get("from_id"))
        elif name == "claim.add":
            added.add(p.get("claim_id"))
            put(ents, p.get("subject_id"))
            put(ents, p.get("object_id"))
            put(claims, p.get("disputes"))
        elif name in ("claim.reinforce", "claim.supersede", "claim.retract", "claim.start_earlier"):
            put(claims, p.get("claim_id"))
            put(claims, p.get("by_claim_id"))
            put(claims, p.get("after_claim_id"))
        elif name == "claim.insert_before":
            put(claims, p.get("before_claim_id"))
            put(claims, p.get("after_claim_id"))
        elif name in ("task.open", "task.close", "task.drop"):
            put(tasks, p.get("task_id"))
    return {"entities": ents, "claims": [c for c in claims if c not in added], "tasks": tasks}


def _validated(extraction: Any, linking: Any) -> Tuple[schemas.Extraction, schemas.Linking]:
    if isinstance(extraction, list):
        extraction, _ = merge_extractions(extraction)
    ext = schemas.validate("Extraction", extraction)
    link = schemas.validate("Linking", linking if linking is not None else {})
    return ext, link  # type: ignore[return-value]


def build_revision(nb: Any, note_id: str, extraction: Any, linking: Any, *, attempt: int,
                   run_id: Optional[str] = None) -> Dict[str, Any]:
    """The revision (ops, summary, warnings) a processed note makes. Pure: nothing is written."""
    note = nb.store.note_row(note_id)
    if note is None:
        raise ValueError("That note is not in this notebook.")
    ext, link = _validated(extraction, linking)
    packet = load_packet(nb, note_id)
    b = _Builder(nb, note, packet or empty_packet())
    flags = extraction.get("warnings") if isinstance(extraction, dict) else None
    b.warnings.extend(str(w) for w in (flags or []) if isinstance(w, str) and w.strip())
    if packet is None and (link.mentions or link.claims or link.tasks):
        b.warnings.append("The note's context was not looked up first, so every reference was treated as new.")
    b.redate(ext)  # first: the mentions, statements and tasks then carry the note's stated date
    b.mentions(ext, link)
    b.claims(ext, link)
    _note_missing_summaries(b, ext, link)
    b.tasks(ext, link)
    b.topics(ext)
    b.ops.append(_op("note.summary", note_id=note_id, summary=ext.summary or None, kind=None, language=None))
    digest = (packet or {}).get("digest")
    omitted = {e: n for e, n in ((digest or {}).get("omitted") or {}).items() if any((n or {}).values())}
    if omitted:  # ADR-0026: what the packet budget left out is said in the revision too, not only in a prompt
        b.warnings.append("packet_omitted: " + "; ".join(
            f"{e} " + ", ".join(f"{k.replace('_omitted', '').replace('_', ' ')} {v}" for k, v in sorted(n.items()) if v)
            for e, n in sorted(omitted.items())) + " (not shown to the linking for space).")
    summary = (link.revision_summary or "").strip() or (ext.summary or "").strip() or "Read the note."
    return {"revision_id": ids.revision_id_for_note(note_id, attempt), "note_id": note_id, "attempt": attempt,
            "at": b.at, "run_id": run_id, "ops": b.ops, "summary": summary,
            "warnings": b.warnings, "context": digest, "used": used_of(b.ops), "stale_context": b.stale_context}


def apply_note_revision(nb: Any, note_id: str, extraction: Any, linking: Any, *, run_id: Optional[str] = None
                        ) -> Dict[str, Any]:
    """Build and apply the revision of a processed note (``note_apply_revision``).

    A note is revised once: when it already has an applied revision, that one is
    finished if needed and returned unchanged (``already_applied``)."""
    if nb.store.note_row(note_id) is None:
        raise ValueError("That note is not in this notebook.")
    nb.recover()
    nb.repair_names_if_pending()  # an upgraded notebook joins the entities that share a canonical name first
    done = nb.store.q1("SELECT revision_id FROM revisions WHERE note_id=? AND action IS NULL AND applied_index=1"
                       " AND abandoned=0 ORDER BY seq DESC LIMIT 1", (note_id,))
    if done is not None:
        out = nb.apply_revision({"revision_id": done[0], "at": nb.store.revision_row(done[0])["at"]})
        out["warnings"] = list(out.get("warnings") or []) + ["This note was already processed; the earlier revision stands."]
        out["already_applied"] = True
        return with_retro_counts(nb, out)
    # ADR 0008 §8: a structurally corrupt answer is invalid; nothing of it is applied (process/validate.py).
    validate.check_apply(nb, note_id, extraction, linking, load_packet(nb, note_id))
    attempt = nb.queue.attempt_for(note_id)
    while True:
        row = nb.store.revision_row(ids.revision_id_for_note(note_id, attempt))
        if row is None or not row["abandoned"]:
            break
        attempt += 1
    rev = build_revision(nb, note_id, extraction, linking, attempt=attempt, run_id=run_id)
    changed = changed_since_read(nb, note_id, rev.get("stale_context"))
    if changed and nb.queue.leased(note_id) and not nb.queue.requeued_stale(note_id):
        # review 4.1: a statement this reading builds on was closed while the note was read; nothing is applied and
        # the drain puts the note back once (``requeue``); the next reading is applied, stale or not
        from ..store.queue import STALE_REQUEUE_ERROR

        return {"revision_id": None, "requeue": True, "reason": STALE_REQUEUE_ERROR, "stale_context": changed,
                "summary": STALE_REQUEUE_ERROR, "warnings": [f"stale_context: {len(changed)} statement(s) this note"
                                                              " named were closed while it was read; read again."]}
    out = _with_stale(with_retro_counts(nb, nb.apply_revision(rev)), rev.get("stale_context"))
    dated = redate_sentence(rev["ops"])  # "Dated 14 March 2019 from the document." for the capture answer
    if dated:
        out["date_sentence"] = dated
    return out


def changed_since_read(nb: Any, note_id: str, claim_ids: Optional[Sequence[str]]) -> List[str]:
    """Of the statements a reading named that are no longer believed, those closed by a revision recorded after the
    note's context packet was stored: the notebook changed while the note was read (review 4.1)."""
    if not claim_ids:
        return []
    row = nb.store.q1("SELECT created_at FROM context_packets WHERE note_id=?", (note_id,))
    if row is None:
        return []
    out = []
    for cid in dict.fromkeys(claim_ids):
        r = nb.store.q1("SELECT v.recorded_at FROM claims c JOIN revisions v ON v.revision_id=c.closed_revision_id"
                        " WHERE c.claim_id=?", (cid,))
        if r is not None and r[0] and r[0] >= row[0]:
            out.append(cid)
    return out


def _with_stale(out: Dict[str, Any], built: Optional[Sequence[str]]) -> Dict[str, Any]:
    """``stale_context`` = the statements this note named that are no longer believed: found when the revision was
    built (closed by the note's date, R1) or when it was applied (closed since the note was read, R2)."""
    stale = list(dict.fromkeys([*(built or []), *(out.get("stale_context") or [])]))
    if stale:
        out["stale_context"] = stale
    return out


def with_retro_counts(nb: Any, out: Dict[str, Any]) -> Dict[str, Any]:
    """The apply result with what the dated placement did, read from the stored revision: the beliefs moved back
    (``claims_started_earlier``), the statements that adopted an existing predicate and the new statements kept
    beside later ones (from the revision's warnings), and ``used`` (:func:`used_of`)."""
    stored = nb.revision(out["revision_id"]) if out.get("revision_id") else None
    ops = (stored or {}).get("ops") or []
    out["claims_started_earlier"] = [op["payload"]["claim_id"] for op in ops if op["op"] == "claim.start_earlier"]
    warnings = list(out.get("warnings") or [])
    out["predicates_adopted"] = sum(1 for w in warnings if str(w).startswith("predicate_adopted:"))
    out["parallel_beliefs"] = sum(1 for w in warnings if str(w).startswith("parallel_belief:"))
    out["used"] = used_of(ops)
    return out


# ============================================================================ ADR 0008: a note understood again
#
# ``note.reinterpret`` (lane R2): the note is read again against its foundation (the context as of its date) and the
# new reading supersedes the previous one's effects. The inverse of every op is in docs/data-model.md
# ("note.reinterpret"); in short: its statements the new reading does not make close with the kind
# ``reinterpreted``, a statement it makes again with another certainty or window is revised in place, statements and
# tasks of other notes it had closed re-open unless the new reading closes them again (or writes on the same slot),
# its reinforcements, mentions, topics and tasks are replaced, entities only it created and nothing references any
# more are retired. Nothing changes when the new reading equals the old one (structural key, no revision written).

from ..store.kg import earlier_evidence_id as kg_earlier_evidence_id  # noqa: E402
from ..store.notes import REINTERPRET_ACTION, REINTERPRETED, TOPICS_PER_NOTE  # noqa: E402


class _ReinterpretBuilder(_Builder):
    """The revision builder of a re-understanding: the note's own previous reading is not context (its statements are
    not "existing" ones to restate, its tasks not open tasks to close, its entities' names are reused, never minted
    again); the statements and tasks the previous reading closed are context (the view adds them)."""

    def __init__(self, nb: Any, note: Any, packet: Dict[str, Any], chain: Sequence[str]) -> None:
        super().__init__(nb, note, packet)
        self.chain = set(chain)
        nid = note["note_id"]
        self.own_claims = {r[0] for r in nb.store.q("SELECT claim_id FROM claims WHERE note_id=?", (nid,))}
        self.own_tasks = {r[0] for r in nb.store.q("SELECT task_id FROM tasks WHERE note_id=?", (nid,))}
        cq = ",".join("?" for _ in self.chain)
        self.own_entities = {r[0] for r in nb.store.q(
            f"SELECT entity_id FROM entities WHERE created_revision_id IN ({cq})", list(self.chain))} if chain else set()

    def alias_matches(self, ref: str) -> List[str]:
        """The close-match review (ADR 0008 §6) leaves out the entries the note's own previous reading created: the
        new reading that names another entry for them is the answer (they are retired when nothing else uses
        them), not a question for review."""
        return [x for x in super().alias_matches(ref) if self.nb.store.canonical(x) not in self.own_entities]

    def existing_claim(self, index: Optional[int]) -> Optional[str]:
        cid = super().existing_claim(index)
        return None if cid in self.own_claims else cid

    def open_task(self, index: Optional[int]) -> Optional[str]:
        lst = self.packet.get("open_tasks") or []
        if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < len(lst)):
            return None
        tid = lst[index]
        if tid in self.own_tasks:
            return None
        row = self.nb.store.q1("SELECT status, closed_revision_id FROM tasks WHERE task_id=?", (tid,))
        if row is None or not (row[0] == "open" or row[1] in self.chain):
            return None
        return tid

    def identical_active(self, subject: str, predicate: str, obj_id: Optional[str], literal: Optional[str]
                         ) -> Optional[str]:
        same = super().identical_active(subject, predicate, obj_id, literal)
        if same is None or same not in self.own_claims:
            return same
        s = self.nb.store.merged_members(self.nb.store.canonical(subject))
        sql = (f"SELECT claim_id FROM claims WHERE status='active' AND valid_until IS NULL AND predicate=? AND"
               f" subject_id IN ({','.join('?' for _ in s)}) AND (note_id IS NULL OR note_id!=?)")
        params: List[Any] = [predicate, *s, self.note_id]
        if obj_id:
            o = self.nb.store.merged_members(self.nb.store.canonical(obj_id))
            sql += f" AND object_id IN ({','.join('?' for _ in o)})"
            params += o
        else:
            sql += " AND object_id IS NULL AND object_literal=?"
            params.append(literal)
        row = self.nb.store.q1(sql + " ORDER BY observed_at LIMIT 1", params)
        return row[0] if row else None

    def lineage(self, anchor: Any) -> List[Any]:
        return [r for r in super().lineage(anchor) if r["claim_id"] not in self.own_claims
                or r["claim_id"] == anchor["claim_id"]]

    def create(self, kind: str, name: str, aliases: Sequence[str], summary: Optional[str]) -> str:
        """A name a live entity already holds is that entity (the canonical-name rule, applied here rather than by
        ``reuse_taken_names`` after the fact, so this reading's statements are compared with its previous ones on
        the same subject instead of becoming reinforcements of them)."""
        holder = self.nb.store.name_key_holder(ids.name_key(name))
        if holder is None:
            return super().create(kind, name, aliases, summary)
        known = self.known_names(holder)
        for alias in [name, *aliases]:
            alias = str(alias or "").strip()
            if alias and ids.fold(alias) not in known:
                known.add(ids.fold(alias))
                self.ops.append(_op("entity.alias", entity_id=holder, alias=alias))
        return holder


def _claim_key(nb: Any, subject: str, predicate: str, obj_id: Optional[str], literal: Optional[str]) -> tuple:
    c = nb.store.canonical
    return (c(subject), predicate, c(obj_id) if obj_id else None, None if obj_id else literal)


def _copy_id(rid: str, claim_id: str) -> str:
    """The AbstractMemory id of a claim's later copy (revised or re-opened by revision ``rid``)."""
    return ids.claim_id(rid, claim_id, "copy", "")


def replay_claim_state(nb: Any, claim_id: str, exclude: Sequence[str]) -> Dict[str, Any]:
    """``{status, valid_until, superseded_by, closed_reason, closed_revision_id}`` of a claim from its own add and
    every applied revision that touched it, leaving out the revisions in ``exclude`` (a superseded reading): the
    state a re-opened statement returns to."""
    row = nb.store.claim_row(claim_id)
    skip = set(exclude)
    until = None
    added = nb.store.revision_row(row["revision_id"])
    for op in json.loads(added["ops_json"]) if added else []:
        p = op.get("payload") or {}
        if op.get("op") == "claim.add" and p.get("claim_id") == claim_id:
            until = p.get("valid_until")
    st: Dict[str, Any] = {"status": "active", "valid_until": until, "superseded_by": None, "closed_reason": None,
                          "closed_revision_id": None}
    for r in nb.store.q("SELECT revision_id, at, ops_json FROM revisions WHERE applied_index=1 AND abandoned=0 AND"
                        " ops_json LIKE ? ORDER BY seq", (f'%"{claim_id}"%',)):
        if r["revision_id"] in skip or r["revision_id"] == row["revision_id"]:
            continue
        for op in json.loads(r["ops_json"]):
            name, p = op.get("op"), op.get("payload") or {}
            close = {"closed_reason": p.get("reason"), "closed_revision_id": r["revision_id"]}
            if name == "claim.insert_before" and p.get("after_claim_id") == claim_id and st["status"] != "retracted":
                if p.get("after_valid_until") and (st["valid_until"] is None or st["valid_until"] > p["after_valid_until"]):
                    st = {**st, "status": "superseded", "valid_until": p["after_valid_until"],
                          "superseded_by": p.get("claim_id"), **close}
            elif p.get("claim_id") != claim_id:
                continue
            elif name == "claim.supersede" and st["status"] == "active":
                st = {**st, "status": "superseded", "valid_until": st["valid_until"] or p.get("valid_until"),
                      "superseded_by": p.get("by_claim_id"), **close}
            elif name == "claim.insert_before" and st["status"] == "active":
                st = {**st, "status": "superseded", "valid_until": p.get("valid_until"),
                      "superseded_by": p.get("before_claim_id"), **close}
            elif name == "claim.retract" and st["status"] != "retracted":
                st = {**st, "status": "retracted", "valid_until": st["valid_until"] or p.get("valid_until") or r["at"],
                      **close}
            elif name == "claim.reinterpret":
                st = {**st, "status": REINTERPRETED, "valid_until": p.get("valid_until"), **close}
            elif name == "claim.reopen":
                st = {k: p.get(k) for k in ("status", "valid_until", "superseded_by", "closed_reason",
                                            "closed_revision_id")}
            elif name == "claim.repoint":
                st = {**st, "superseded_by": p.get("superseded_by")}
    return st


def replay_claim_start(nb: Any, claim_id: str, exclude: Sequence[str]) -> Dict[str, Optional[str]]:
    """``{observed_at, valid_from}`` of a claim from its own add and the start moves of every applied revision
    (``claim.start_earlier``: the earlier of the two; ``claim.start_restore``: set), leaving out the revisions in
    ``exclude`` (a superseded reading): the start a belief returns to when the earlier evidence is withdrawn."""
    row = nb.store.claim_row(claim_id)
    start: Dict[str, Optional[str]] = {"observed_at": row["observed_at"], "valid_from": row["valid_from"]}
    added = nb.store.revision_row(row["revision_id"])
    for op in json.loads(added["ops_json"]) if added else []:
        p = op.get("payload") or {}
        if op.get("op") == "claim.add" and p.get("claim_id") == claim_id:
            start = {"observed_at": p.get("observed_at"), "valid_from": p.get("valid_from")}
    skip = set(exclude)
    for r in nb.store.q("SELECT revision_id, ops_json FROM revisions WHERE applied_index=1 AND abandoned=0 AND"
                        " ops_json LIKE ? ORDER BY seq", (f'%"{claim_id}"%',)):
        if r["revision_id"] in skip or r["revision_id"] == row["revision_id"]:
            continue
        for op in json.loads(r["ops_json"]):
            p = op.get("payload") or {}
            if p.get("claim_id") != claim_id:
                continue
            if op.get("op") == "claim.start_earlier":
                if p.get("observed_at") and (not start["observed_at"] or p["observed_at"] < start["observed_at"]):
                    start["observed_at"] = p["observed_at"]
                if start["valid_from"] and p.get("valid_from") and p["valid_from"] < start["valid_from"]:
                    start["valid_from"] = p["valid_from"]
            elif op.get("op") == "claim.start_restore":
                start = {"observed_at": p["observed_at"], "valid_from": p.get("valid_from")}
    return start


def _claim_fields(row: Any) -> Dict[str, Any]:
    return {k: row[k] for k in ("subject_id", "predicate", "object_id", "object_literal", "certainty", "stance",
                                "observed_at", "valid_from", "valid_until", "evidence", "note_id")}


def _read_as(nb: Any, b: Any, ext: Any, entity_id: str) -> List[str]:
    """The entries the new reading uses for the words that made a retired entry (the retired entry's page says it
    was retired into them): a mention of the new reading whose own name or alias has the retired entry's name or
    one of its aliases as its lookup key (the exact case-folded rule of candidate generation, ADR 0004; the
    linking decided which entry those words are). Empty when the new reading no longer names it."""
    s = nb.store
    row = s.entity_row(entity_id)
    keys = {ids.name_key(x) for x in (row["name"], row["display_name"], *s.aliases(entity_id)) if x}
    keys.discard("")
    out: List[str] = []
    for m in ext.mentions:
        eid = b.refmap.get(m.ref)
        if not eid or not keys & {ids.name_key(x) for x in (m.name, *(m.aliases or [])) if x}:
            continue
        eid = s.canonical(eid)
        if eid != entity_id and eid not in out:
            out.append(eid)
    return out


def plan_reinterpretation(nb: Any, note_id: str, extraction: Any, linking: Any, *, run_id: Optional[str] = None,
                          reason: Optional[str] = None) -> Dict[str, Any]:
    """The re-understanding of a note (pure: nothing is written). ``{unchanged, summary_only, previous_revision_id,
    revision (when changed), diff: {claims_closed, claims_revised, claims_reopened, claims_repointed,
    reinforcements_dropped, tasks_reopened, tasks_retired, entities_retired}, warnings}``."""
    from ..store.names import reuse_taken_names

    s = nb.store
    note = s.note_row(note_id)
    if note is None:
        raise ValueError("That note is not in this notebook.")
    current = s.current_interpretation(note_id)
    if current is None:
        raise ValueError("This note has not been understood yet, so it cannot be understood again; it is understood"
                         " when its turn in the queue comes.")
    chain = s.interpretation_chain(note_id)
    packet = load_packet(nb, note_id)
    if packet is None:
        raise ValueError("Look up the note's context first (note_context): a note is understood again against what"
                         " was known at its date.")
    ext, link = _validated(extraction, linking)
    b = _ReinterpretBuilder(nb, note, packet, chain)
    flags = extraction.get("warnings") if isinstance(extraction, dict) else None
    b.warnings.extend(str(w) for w in (flags or []) if isinstance(w, str) and w.strip())
    b.redate(ext)
    b.mentions(ext, link)
    b.claims(ext, link)
    _note_missing_summaries(b, ext, link)
    b.tasks(ext, link)
    b.topics(ext)
    b.ops.append(_op("note.summary", note_id=note_id, summary=ext.summary or None, kind=None, language=None))
    rid = ids.revision_id_for_action(REINTERPRET_ACTION, note_id, current)
    built = reuse_taken_names(s, {"revision_id": rid, "note_id": note_id, "ops": b.ops, "warnings": b.warnings})
    chain_set = set(chain)
    cq = ",".join("?" for _ in chain)

    own_rows = list(s.q("SELECT * FROM claims WHERE note_id=? AND status!=?", (note_id, REINTERPRETED)))
    own_by_key = {_claim_key(nb, r["subject_id"], r["predicate"], r["object_id"], r["object_literal"]): r
                  for r in own_rows}
    own_ids = {r["claim_id"] for r in own_rows}
    cross_claims = {r["claim_id"]: r for r in s.q(
        f"SELECT * FROM claims WHERE closed_revision_id IN ({cq}) AND (note_id IS NULL OR note_id!=?)",
        [*chain, note_id])} if chain else {}
    my_reinforced = {r[0] for r in s.q(f"SELECT claim_id FROM claim_reinforcements WHERE revision_id IN ({cq})",
                                       chain)} if chain else set()
    own_tasks = {r["task_id"]: r for r in s.q("SELECT * FROM tasks WHERE note_id=? AND status!='retired'", (note_id,))}
    cross_tasks = {r["task_id"]: r for r in s.q(
        f"SELECT * FROM tasks WHERE closed_revision_id IN ({cq}) AND (note_id IS NULL OR note_id!=?)",
        [*chain, note_id])} if chain else {}

    claim_map: Dict[str, str] = {}
    kept, revised, slots = set(), [], {}
    kept_reinf, kept_cross, kept_tasks, kept_cross_tasks = set(), set(), set(), set()
    new_ids: set = set()
    changes = 0  # effects the new reading has that the old one did not
    out: List[Dict[str, Any]] = []
    mentions: set = set()
    topics: set = set()
    summary_ops = 0
    for op in built["ops"]:
        name, p = op["op"], dict(op.get("payload") or {})
        for f in ("claim_id", "by_claim_id", "before_claim_id", "after_claim_id", "disputes"):
            if p.get(f) in claim_map:
                p[f] = claim_map[p[f]]
        if name == "claim.add":
            if p.get("object_id") and p["object_id"] == p["subject_id"]:
                continue
            key = _claim_key(nb, p["subject_id"], p["predicate"], p.get("object_id"), p.get("object_literal"))
            own = own_by_key.get(key)
            if own is not None:
                claim_map[p["claim_id"]] = own["claim_id"]
                kept.add(own["claim_id"])
                slots.setdefault((key[0], key[1]), own["claim_id"])
                closed = own["closed_revision_id"] is not None
                now = (p["certainty"], p.get("stance") or "assert", p.get("valid_from"), p.get("evidence"),
                       None if closed else p.get("valid_until"))
                was = (own["certainty"], own["stance"] or "assert", own["valid_from"], own["evidence"],
                       None if closed else own["valid_until"])
                if now != was:
                    copy = _copy_id(rid, own["claim_id"])
                    assertion = {**_claim_fields(own), "claim_id": copy, "of_claim_id": own["claim_id"],
                                 "certainty": p["certainty"], "stance": p.get("stance") or "assert",
                                 "valid_from": p.get("valid_from"), "evidence": p.get("evidence"),
                                 "valid_until": own["valid_until"] if closed else p.get("valid_until")}
                    out.append(_op("claim.revise", claim_id=own["claim_id"], certainty=p["certainty"],
                                   stance=p.get("stance") or "assert", valid_from=p.get("valid_from"),
                                   valid_until=p.get("valid_until"), evidence=p.get("evidence"), assertion_id=copy,
                                   previous_assertion_id=own["assertion_id"] or own["claim_id"],
                                   empty_at=own["valid_from"] or own["observed_at"], assertion=assertion,
                                   previous={"certainty": own["certainty"], "stance": own["stance"],
                                             "valid_from": own["valid_from"], "valid_until": own["valid_until"],
                                             "evidence": own["evidence"]},
                                   reason=reason or "read again"))
                    revised.append(own["claim_id"])
                continue
            cid = p["claim_id"]
            if s.claim_row(cid) is not None or cid in new_ids:  # an id an earlier reading of this note used (review 1.2)
                new = ids.claim_id(f"{note_id}@{rid}", p["subject_id"], p["predicate"], p.get("object_id") or
                                   p.get("object_literal"))
                claim_map[cid] = new
                p["claim_id"] = cid = new
            new_ids.add(cid)
            slots.setdefault((key[0], key[1]), cid)
            changes += 1
            out.append({"op": name, "payload": p})
            continue
        if name == "claim.reinforce":
            target = p["claim_id"]
            if target in own_ids:
                kept.add(target)
                continue
            if target in my_reinforced:
                kept_reinf.add(target)
                continue
            changes += 1
        elif name in ("claim.supersede", "claim.retract"):
            target = p["claim_id"]
            if target in cross_claims:
                kept_cross.add(target)
                continue
            if target in own_ids:
                continue
            changes += 1
        elif name == "claim.insert_before":
            if p["claim_id"] in own_ids:
                continue  # its statement was kept: it is already placed
            if p.get("after_claim_id") in cross_claims:
                kept_cross.add(p["after_claim_id"])
            changes += 1
        elif name == "entity.mention":
            mentions.add(p["entity_id"])
        elif name == "task.open":
            if p["task_id"] in own_tasks:
                kept_tasks.add(p["task_id"])
                continue
            changes += 1
        elif name == "task.close":
            tid = p["task_id"]
            if tid in cross_tasks:
                kept_cross_tasks.add(tid)
                continue
            if tid in own_tasks:
                kept_tasks.add(tid)
                if own_tasks[tid]["status"] != "open":
                    continue
            changes += 1
        elif name == "topic.assign":
            t = ids.canonical_name(p["topic"])
            if t not in topics and len(topics) >= TOPICS_PER_NOTE:  # the processing cap (op_topic_assign's)
                built["warnings"].append(f"topics_not_assigned: a note keeps the first {TOPICS_PER_NOTE} topics from"
                                         f" processing; not assigned: {p['topic']}.")
                continue
            topics.add(t)
        elif name in ("entity.create", "entity.merge", "claim.revise"):
            changes += 1
        elif name in ("entity.summary", "note.summary", "entity.alias", "entity.rename"):
            summary_ops += 1
        out.append({"op": name, "payload": p})

    pre: List[Dict[str, Any]] = [_op("note.reinterpret", note_id=note_id, supersedes=current,
                                     reason=reason or "understood again", by_note_ids=[])]
    diff: Dict[str, List[str]] = {k: [] for k in ("claims_closed", "claims_revised", "claims_reopened",
                                                  "claims_repointed", "reinforcements_dropped", "starts_restored",
                                                  "tasks_reopened", "tasks_retired", "entities_retired")}
    diff["claims_revised"] = list(revised)
    for r in own_rows:
        if r["claim_id"] not in kept:
            pre.append(_op("claim.reinterpret", claim_id=r["claim_id"], valid_until=r["valid_from"] or r["observed_at"],
                           assertion_id=r["assertion_id"] or r["claim_id"], reason=reason or "understood again"))
            diff["claims_closed"].append(r["claim_id"])
    post: List[Dict[str, Any]] = []
    for cid, x in cross_claims.items():
        slot = (nb.store.canonical(x["subject_id"]), x["predicate"])
        if cid in kept_cross or slot in slots:
            by = slots.get(slot)
            if by and x["superseded_by"] != by and x["superseded_by"] not in kept and x["superseded_by"] not in new_ids:
                post.append(_op("claim.repoint", claim_id=cid, superseded_by=by, previous=x["superseded_by"]))
                diff["claims_repointed"].append(cid)
            continue
        st = replay_claim_state(nb, cid, chain)
        copy = _copy_id(rid, cid)
        pre.append(_op("claim.reopen", claim_id=cid, closed_by_revision=x["closed_revision_id"], assertion_id=copy,
                       previous_assertion_id=x["assertion_id"] or cid, empty_at=x["valid_from"] or x["observed_at"],
                       assertion={**_claim_fields(x), "claim_id": copy, "of_claim_id": cid,
                                  "valid_until": st["valid_until"]},
                       reason="the reading that closed it was withdrawn", **st))
        diff["claims_reopened"].append(cid)
    for cid in sorted(my_reinforced - kept_reinf):
        for (rv,) in s.q(f"SELECT revision_id FROM claim_reinforcements WHERE claim_id=? AND revision_id IN ({cq})",
                         [cid, *chain]):
            pre.append(_op("reinforcement.drop", claim_id=cid, revision_id=rv))
        diff["reinforcements_dropped"].append(cid)
    # the inverse of claim.start_earlier (R1): a belief the withdrawn reading moved back as earlier evidence and the
    # new reading no longer restates returns to the start replayed without it; its additive earlier-evidence
    # assertions close empty in AbstractMemory, and the belief it narrowed re-opens (claim.reopen above)
    still = {o["payload"].get("claim_id") for o in built["ops"] if o["op"] in ("claim.reinforce", "claim.start_earlier")}
    moved: Dict[str, List[Dict[str, Any]]] = {}
    for rv in chain:
        r = s.revision_row(rv)
        for op in json.loads(r["ops_json"]) if r is not None and r["applied_index"] else []:
            if op.get("op") == "claim.start_earlier" and (op.get("payload") or {}).get("claim_id"):
                moved.setdefault(op["payload"]["claim_id"], []).append({**op["payload"], "_note": r["note_id"]})
    for cid in sorted(moved):
        if cid in still or cid in claim_map or s.claim_row(cid) is None:
            continue
        back = replay_claim_start(nb, cid, chain)
        evidence = []
        for mp in moved[cid]:
            aid = kg_earlier_evidence_id(mp, mp["_note"])
            if aid:
                evidence.append({"assertion_id": aid, "empty_at": mp["valid_from"]})
        pre.append(_op("claim.start_restore", claim_id=cid, observed_at=back["observed_at"],
                       valid_from=back["valid_from"], earlier_assertions=evidence,
                       reason=reason or "the earlier evidence was understood again"))
        diff["starts_restored"].append(cid)
    for tid, t in cross_tasks.items():
        if tid not in kept_cross_tasks:
            pre.append(_op("task.reopen", task_id=tid, closed_revision_id=t["closed_revision_id"]))
            diff["tasks_reopened"].append(tid)
    for tid, t in own_tasks.items():
        if tid not in kept_tasks:
            post.append(_op("task.retire", task_id=tid, reason=reason or "understood again"))
            diff["tasks_retired"].append(tid)
        elif t["status"] != "open" and t["closed_revision_id"] in chain_set and not any(
                o["op"] == "task.close" and o["payload"].get("task_id") == tid for o in built["ops"]):
            pre.append(_op("task.reopen", task_id=tid, closed_revision_id=t["closed_revision_id"]))
            diff["tasks_reopened"].append(tid)
    referenced: set = set(mentions)
    for o in out:
        p = o["payload"]
        referenced.update(x for x in (p.get("subject_id"), p.get("object_id"), p.get("entity_id"), p.get("into_id"))
                          if x)
    for r in own_rows:
        if r["claim_id"] in kept:
            referenced.update(x for x in (r["subject_id"], r["object_id"]) if x)
    created = [r[0] for r in s.q(f"SELECT entity_id FROM entities WHERE created_revision_id IN ({cq}) AND merged_into IS"
                                 " NULL AND retired_revision_id IS NULL ORDER BY entity_id", chain)] if chain else []
    for eid in created:
        if eid in referenced or nb.store.canonical(eid) in referenced:
            continue
        members = s.merged_members(eid)
        mq = ",".join("?" for _ in members)
        # a close-match review item (R1, ADR 0008 §6) between this entry and one the new reading now uses is the
        # question the new reading answered: it does not keep the entry alive (the review skips retired entries)
        rq = ",".join("?" for _ in referenced) or "NULL"
        elsewhere = s.q1(f"SELECT 1 FROM entity_notes WHERE entity_id IN ({mq}) AND note_id!=? LIMIT 1",
                         [*members, note_id]) or s.q1(
            f"SELECT 1 FROM claims WHERE (subject_id IN ({mq}) OR object_id IN ({mq})) AND (note_id IS NULL OR"
            f" note_id!=?) AND status!=? AND NOT (predicate=? AND (subject_id IN ({rq}) OR object_id IN ({rq})))"
            f" LIMIT 1", [*members, *members, note_id, REINTERPRETED, CLOSE_MATCH, *referenced, *referenced])
        if elsewhere:
            continue
        post.append(_op("entity.retire", entity_id=eid, reason=reason or "only an earlier reading of this note made it",
                        read_as=_read_as(nb, b, ext, eid)))
        diff["entities_retired"].append(eid)

    old_mentions = {r[0] for r in s.q("SELECT entity_id FROM entity_notes WHERE note_id=?", (note_id,))}
    old_topics = {r[0] for r in s.q("SELECT topic FROM note_topics WHERE note_id=? AND assigned_by='revision'",
                                    (note_id,))}
    unchanged = (changes == 0 and not any(diff.values()) and mentions == old_mentions and topics == old_topics)
    result: Dict[str, Any] = {"unchanged": unchanged, "summary_only": unchanged and summary_ops > 0,
                              "previous_revision_id": current, "diff": diff, "warnings": list(built["warnings"]),
                              "stale_context": list(b.stale_context)}
    if unchanged:
        return result
    summary = (link.revision_summary or "").strip() or (ext.summary or "").strip() or "Read the note again."
    result["revision"] = {"revision_id": rid, "note_id": note_id, "action": REINTERPRET_ACTION, "target": note_id,
                          "at": b.at, "run_id": run_id, "ops": [*pre, *out, *post], "summary": summary,
                          "warnings": list(built["warnings"]),
                          "source": {"client": "consolidation", "reason": reason or "understood again"}}
    return result


def apply_reinterpretation(nb: Any, note_id: str, extraction: Any, linking: Any, *, run_id: Optional[str] = None,
                           reason: Optional[str] = None, pass_id: Optional[str] = None) -> Dict[str, Any]:
    """Understand an already understood note again (``note_apply_revision`` with ``reinterpret``, ADR 0008): build
    the ``note.reinterpret`` revision and apply it, or record ``unchanged`` and write nothing; either way the note's
    marks this pass took (or, without a pass, every mark it had when its context was read) are consumed. Fails
    loudly when the installed AbstractMemory lacks the closure kind ``reinterpreted``."""
    from ..store.kg import require_reinterpreted

    require_reinterpreted()
    nb.recover()
    # ADR 0008 §8 (R4): a structurally corrupt answer is invalid for a re-reading too; nothing of it is applied
    validate.check_apply(nb, note_id, extraction, linking, load_packet(nb, note_id))
    plan = plan_reinterpretation(nb, note_id, extraction, linking, run_id=run_id, reason=reason)
    packet_row = nb.store.q1("SELECT created_at FROM context_packets WHERE note_id=?", (note_id,))
    before = packet_row[0] if packet_row else None
    if plan["unchanged"]:
        # ADR 0008 §4a: an unchanged re-reading writes no revision, but it WAS read against this packet (a propagated
        # one against the source ideas' state as known then, ``related``): the current reading's record becomes what
        # it saw, so a later wave from the same, unchanged idea is settled with no call (_keep_related)
        _consume(nb, note_id, pass_id, "unchanged", None, before,
                 keep_related=(plan["previous_revision_id"], load_packet(nb, note_id)))
        if pass_id:
            nb.queue.release(note_id, "reinterpret", pass_id)
        return {"revision_id": None, "unchanged": True, "summary_only": plan["summary_only"],
                "previous_revision_id": plan["previous_revision_id"], "summary": "Understood again: nothing changed.",
                "entities_created": [], "entities_merged": [], "claims_added": [], "claims_reinforced": [],
                "claims_superseded": [], "claims_retracted": [], "tasks_opened": [], "tasks_closed": [],
                "claims_inserted_before": [], "retrospective": [], "warnings": plan["warnings"], "marked": 0,
                "reinterpret_marked": {"count": 0, "notes": [], "by_entity": []}}
    out = _with_stale(with_retro_counts(nb, nb.apply_revision(plan["revision"])), plan["stale_context"])
    _consume(nb, note_id, pass_id, "reinterpreted", out["revision_id"], before)
    if pass_id:
        nb.queue.release(note_id, "reinterpret", pass_id)  # the stage's claim (consolidation.reinterpret_next)
    out.update({"reinterpreted": True, "unchanged": False, "previous_revision_id": plan["previous_revision_id"],
                "diff": plan["diff"]})
    return out


def _consume(nb: Any, note_id: str, pass_id: Optional[str], outcome: str, revision_id: Optional[str],
             before: Optional[str], keep_related: Optional[Tuple[Optional[str], Optional[Dict[str, Any]]]] = None) -> None:
    s = nb.store
    with s.tx() as c:
        if keep_related is not None:
            _keep_related(c, s, *keep_related)
        if pass_id:
            s.consume_marks(note_id, pass_id, outcome, revision_id, c=c)
            return
        now = ids.utc_now()
        c.execute("UPDATE reinterpret_marks SET consumed_at=?, outcome=?, consumed_revision_id=?,"
                  " pass_id=COALESCE(pass_id, 'direct'), taken_at=COALESCE(taken_at, ?) WHERE note_id=? AND"
                  " consumed_at IS NULL AND (pass_id IS NOT NULL OR marked_at<=?)",
                  (now, outcome, revision_id, now, note_id, before or now))


def _keep_related(c: Any, store: Any, revision_id: Optional[str], packet: Optional[Dict[str, Any]]) -> None:
    """An unchanged re-reading writes no revision (R2), but it WAS read against the stored packet: the dependency
    record of the note's current reading ``revision_id`` becomes what that re-reading saw (:func:`context_digest`
    of the packet, its ``related`` included: ADR 0008 §4a, each source idea's state as known at the note's date),
    so the no-call check of a later wave compares against the foundation the reading was last confirmed on. Only
    ``related`` would not do: an earlier note that arrived since (a similar note the re-reading was shown) left the
    old record stale, and every later wave paid a call again. A reading without a record (``context_unknown``,
    before schema 3) stays without one."""
    from ..store.notebook import context_digest

    if not revision_id or packet is None:
        return
    row = c.execute("SELECT r.context_json, n.note_id FROM revisions r JOIN notes n ON n.note_id=r.note_id WHERE"
                    " r.revision_id=?", (revision_id,)).fetchone()
    stored = json.loads(row[0]) if row is not None and row[0] else None
    if not isinstance(stored, dict):
        return
    record = context_digest(store, store.note_row(row[1]), packet)
    c.execute("UPDATE revisions SET context_json=? WHERE revision_id=?",
              (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")), revision_id))


def reinterpret_view(nb: Any, note_id: str, out: Dict[str, Any]) -> Dict[str, Any]:
    """The context of a note understood again (ADR 0008): its own previous reading is not shown (its statements and
    tasks), and the statements and tasks of other notes that the previous reading closed are shown again as they
    would stand without it (they re-open unless the new reading closes them). Edits the stored packet so every index
    stays valid. Fails loudly when the context has not the expected keys (seam with lane R1)."""
    from .context import claim_view, save_packet

    for k in ("existing_claims", "candidate_entities", "open_tasks"):
        if k not in out:
            raise RuntimeError(f"note_context returned no {k!r}; the re-understanding view cannot be built (seam R1).")
    s = nb.store
    chain = s.interpretation_chain(note_id)
    if not chain:
        return out
    packet = load_packet(nb, note_id)
    if packet is None:
        raise RuntimeError("note_context stored no packet for this note.")
    own = {r[0] for r in s.q("SELECT claim_id FROM claims WHERE note_id=?", (note_id,))}
    own_tasks = {r[0] for r in s.q("SELECT task_id FROM tasks WHERE note_id=?", (note_id,))}
    out = dict(out)
    out["existing_claims"] = [v for v in out["existing_claims"] if v.get("claim_id") not in own]
    cands = []
    for c in out["candidate_entities"]:
        c = dict(c)
        c["active_claims"] = [v for v in c.get("active_claims") or [] if v.get("claim_id") not in own]
        cands.append(c)
    out["candidate_entities"] = cands
    out["open_tasks"] = [t for t in out["open_tasks"] if t.get("task_id") not in own_tasks]
    cq = ",".join("?" for _ in chain)
    by_entity = {c["entity_id"]: c for c in cands}
    for r in s.q(f"SELECT * FROM claims WHERE closed_revision_id IN ({cq}) AND (note_id IS NULL OR note_id!=?) AND"
                 f" status!=? ORDER BY observed_at", [*chain, note_id, REINTERPRETED]):
        if r["claim_id"] in packet["existing_claims"]:
            continue
        idx = len(packet["existing_claims"])
        packet["existing_claims"].append(r["claim_id"])
        view = claim_view(nb, r, idx)
        out["existing_claims"].append(view)
        for e in (r["subject_id"], r["object_id"]):
            card = by_entity.get(s.canonical(e)) if e else None
            if card is not None:
                card["active_claims"].append(view)
    for t in s.q(f"SELECT * FROM tasks WHERE closed_revision_id IN ({cq}) AND (note_id IS NULL OR note_id!=?)",
                 [*chain, note_id]):
        if t["task_id"] in packet["open_tasks"]:
            continue
        idx = len(packet["open_tasks"])
        packet["open_tasks"].append(t["task_id"])
        out["open_tasks"].append({"index": idx, "task_id": t["task_id"], "text": t["text"], "opened_at": t["opened_at"],
                                  "due": t["due"], "note_id": t["note_id"]})
    save_packet(nb, note_id, packet)
    return out
