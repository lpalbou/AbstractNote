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
- time: a note OLDER than the statement it supersedes (or contradicts, unless the
  model marked it disputed within :data:`DISPUTE_WINDOW`) never closes it: its
  statement is placed as the earlier belief (``claim.add`` + ``claim.insert_before``);
  a document date stated in the text of a note whose date is approximate moves the
  note first (``note.redate``); no window ever ends before it starts;
- tasks: ``task.close`` of the open task named by index, only with the note's own
  words saying it is done and never from a note older than the task (eval2 D3),
  else ``task.open`` (and ``task.close`` when the note reports it done);
- topics -> ``topic.assign``; the note summary -> ``note.summary``.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import ids
from ..ingest.dates import CONTENT, date_basis, is_approximate
from ..ingest.normalise import normalize_topic, text_norm
from ..store.names import strip_kind_suffix
from ..store.notes import _TakenIds
from . import schemas
from .context import empty_packet, load_packet, words
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
            to = ids.normalize_time(dd.iso, field="document date")
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
        self.ops.append(_op("note.redate", note_id=self.note_id, **{"from": self.at}, to=to, basis=CONTENT,
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
                    cid = ids.claim_id(self.note_id, eid, CLOSE_MATCH, close_to)
                    self.claim_ids.add(cid)
                    self.ops.append(_op("claim.add", claim_id=cid, subject_id=eid, predicate=CLOSE_MATCH,
                                        object_id=close_to, object_literal=None, certainty="possible",
                                        stance="assert", observed_at=self.at, valid_from=self.at, valid_until=None,
                                        evidence=m.name, note_id=self.note_id))
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
        about = json.loads(self.note["about_json"]) if self.note["about_json"] else None
        if about and about.get("kind") == "entity" and self.nb.store.entity_row(about["id"]) is not None:
            self.mention(self.nb.store.canonical(about["id"]))

    # ---------------------------------------------------------------- claims
    def identical_active(self, subject: str, predicate: str, obj_id: Optional[str], literal: Optional[str]
                         ) -> Optional[str]:
        s = self.nb.store.merged_members(self.nb.store.canonical(subject))
        sql = (f"SELECT claim_id FROM claims WHERE status='active' AND valid_until IS NULL AND predicate=? AND"
               f" subject_id IN ({','.join('?' for _ in s)})")
        params: List[Any] = [predicate, *s]
        if obj_id:
            o = self.nb.store.merged_members(self.nb.store.canonical(obj_id))
            sql += f" AND object_id IN ({','.join('?' for _ in o)})"
            params += o
        else:
            sql += " AND object_id IS NULL AND object_literal=?"
            params.append(literal)
        row = self.nb.store.q1(sql + " ORDER BY observed_at LIMIT 1", params)
        return row[0] if row else None

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
            cid = ids.claim_id(self.note_id, subject, predicate, obj_id or literal)
            if cid in self.claim_ids:
                continue
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
            if relation in ("supersedes", "contradicts") and not self.same_statement_slot(existing, subject, predicate):
                # eval2 D2: only a statement about the same subject and relation can replace or dispute it
                self.warnings.append(f"Statement {i} was linked to an existing statement about another subject or "
                                     "relation; it was kept as new.")
                relation, existing = "new", None
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
            same = self.identical_active(subject, predicate, obj_id, literal)
            reason = (lc.reason if lc and lc.reason else "")
            if relation == "restates":
                self.ops.append(_op("claim.reinforce", claim_id=existing, note_id=self.note_id))
                continue
            if same:
                # The very statement is already believed: reinforce it instead of duplicating it.
                self.ops.append(_op("claim.reinforce", claim_id=same, note_id=self.note_id))
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
            older = existing is not None and self.at < self.nb.store.claim_row(existing)["observed_at"]
            if relation == "contradicts" and older and not self.still_disputed(c.certainty, existing):
                relation = "supersedes"  # an older note's view is the earlier belief, not a dispute
            if relation == "contradicts":
                payload["certainty"] = "disputed"
                payload["disputes"] = existing
                self.ops.append(_op("claim.add", **payload))
            elif relation == "supersedes" and older:
                self.insert_before(payload, existing, reason or "an earlier belief, replaced later")
            elif relation == "supersedes":
                self.ops.append(_op("claim.add", **payload))
                reason = reason or "a later note changed it"
                if c.stance == "retract":
                    self.ops.append(_op("claim.retract", claim_id=existing, reason=reason,
                                        valid_until=self.closing_at(existing)))
                else:
                    self.ops.append(_op("claim.supersede", claim_id=existing, by_claim_id=cid, reason=reason,
                                        valid_until=self.closing_at(existing)))
            else:
                self.ops.append(_op("claim.add", **payload))

    # ---------------------------------------------------------------- time-aware placement
    def same_statement_slot(self, existing: Optional[str], subject: str, predicate: str) -> bool:
        row = self.nb.store.claim_row(existing) if existing else None
        if row is None:
            return False
        canon = self.nb.store.canonical
        return canon(row["subject_id"]) == canon(subject) and row["predicate"] == predicate

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
        """The beliefs one statement belongs to: the claims linked to it by supersession (both ways), on the same
        subject and predicate, retracted ones left out."""
        s = self.nb.store
        members = set(s.merged_members(s.canonical(anchor["subject_id"])))
        seen = {anchor["claim_id"]: anchor}
        todo = [anchor]
        while todo:
            x = todo.pop()
            linked = list(s.q("SELECT * FROM claims WHERE superseded_by=?", (x["claim_id"],)))
            if x["superseded_by"]:
                nxt = s.claim_row(x["superseded_by"])
                if nxt is not None:
                    linked.append(nxt)
            for r in linked:
                if r["claim_id"] in seen or r["status"] == "retracted" or r["predicate"] != anchor["predicate"] \
                        or r["subject_id"] not in members:
                    continue
                seen[r["claim_id"]] = r
                todo.append(r)
        return list(seen.values())

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
    summary = (link.revision_summary or "").strip() or (ext.summary or "").strip() or "Read the note."
    return {"revision_id": ids.revision_id_for_note(note_id, attempt), "note_id": note_id, "attempt": attempt,
            "at": b.at, "run_id": run_id, "ops": b.ops, "summary": summary,
            "warnings": b.warnings}


def apply_note_revision(nb: Any, note_id: str, extraction: Any, linking: Any, *, run_id: Optional[str] = None,
                        ledger: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build and apply the revision of a processed note (``note_apply_revision``).

    ``ledger`` is the address of the step applying it (``{run_id, seq?, step_id?}``, backlog 0037); None lets
    :meth:`Notebook.apply_revision` stamp the runtime step it runs in. A note is revised once: when it already
    has an applied revision, that one is finished if needed and returned unchanged (``already_applied``)."""
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
        return out
    attempt = nb.queue.attempt_for(note_id)
    while True:
        row = nb.store.revision_row(ids.revision_id_for_note(note_id, attempt))
        if row is None or not row["abandoned"]:
            break
        attempt += 1
    rev = build_revision(nb, note_id, extraction, linking, attempt=attempt, run_id=run_id)
    if ledger is not None:
        rev["ledger"] = ledger
    return nb.apply_revision(rev)
