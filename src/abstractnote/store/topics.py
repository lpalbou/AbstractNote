"""One spelling per topic (``abstractnote normalize-topics``, eval2 D13).

Topics are stored under their canonical name since processing assigns them so (``NoteStore.assign_topic``);
topics stored earlier keep their old spelling. :func:`plan` groups the stored topics that are one topic: the same
:func:`abstractnote.ids.canonical_name` (``bug-fix``, ``Bug_Fix``, ``bug fix``), and a plural whose last word ends
in ``s`` or ``es`` when the singular is itself a stored topic (``bug_fixes`` -> ``bug_fix``; the singular's last
word must keep at least :data:`PLURAL_MIN_STEM` letters, so ``is`` never folds into ``i``). That is spelling,
not meaning: anything else (``gateway`` versus ``abstractgateway``) is not merged here (ADR 0004).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import ids

ACTION = "topic.normalize"
PLURAL_MIN_STEM = 3


def _singular(key: str, keys: set) -> str:
    last = key.rsplit("_", 1)[-1]
    for cut in (1, 2):
        stem = key[:-cut]
        if last.endswith("s" if cut == 1 else "es") and len(last) - cut >= PLURAL_MIN_STEM and stem in keys:
            return stem
    return key


def plan(store: Any) -> List[Dict[str, Any]]:
    """``[{into, from: [stored spellings], notes}]``: every stored topic that is not yet its group's one name."""
    topics = [r[0] for r in store.q("SELECT DISTINCT topic FROM note_topics ORDER BY topic")]
    keys = {ids.canonical_name(t) for t in topics if ids.canonical_name(t)}
    groups: Dict[str, List[str]] = {}
    for t in topics:
        key = ids.canonical_name(t)
        if key:
            groups.setdefault(_singular(key, keys), []).append(t)
    out = []
    for into, members in sorted(groups.items()):
        variants = sorted(m for m in members if m != into)
        if variants:
            marks = ",".join("?" for _ in variants)
            notes = store.q1(f"SELECT COUNT(DISTINCT note_id) FROM note_topics WHERE topic IN ({marks})", variants)[0]
            out.append({"into": into, "from": variants, "notes": int(notes)})
    return out


def normalize_topics(nb: Any, account: Optional[str] = None, *, client: str = "cli", dry_run: bool = False
                     ) -> Dict[str, Any]:
    """Move every note from a variant to its group's name in ONE user revision (``topic.unassign`` the stored
    spelling, ``topic.assign`` the name): ``{dry_run, groups, moved, revision_id}``. A second run finds nothing."""
    groups = plan(nb.store)
    out: Dict[str, Any] = {"dry_run": dry_run, "groups": groups, "moved": 0, "revision_id": None}
    if dry_run or not groups:
        return out
    ops: List[Dict[str, Any]] = []
    for g in groups:
        for variant in g["from"]:
            for (note_id,) in nb.store.q("SELECT note_id FROM note_topics WHERE topic=? ORDER BY note_id", (variant,)):
                ops.append({"op": "topic.unassign", "payload": {"note_id": note_id, "topic": variant}})
                ops.append({"op": "topic.assign", "payload": {"note_id": note_id, "topic": g["into"]}})
    n = sum(1 for o in ops if o["op"] == "topic.unassign")
    summary = f"Gave {len(groups)} topic{'s' if len(groups) != 1 else ''} one spelling ({n} filings moved)."
    res = nb._user_revision(ACTION, "topics", ops, account, summary, client=client)
    out.update(moved=n, revision_id=res["revision_id"])
    return out
