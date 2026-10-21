"""Archive repeated insights (W28): ``abstractnote archive-insights --repeated`` and
``POST /note/insights/archive-repeated``.

Reflection wrote the same insight again on later passes (eval2 D5: one reminder nine times). Insights are repeats of
one another when they have the same kind, the same title (case-folded, whitespace collapsed: an exact key, never a
judgement of meaning, ADR 0004) and the same subject (the same evidence notes and entities, as
:func:`abstractnote.recall.insights.write_insight` compares them). Of each group the OLDEST unacknowledged insight is
kept; the other unacknowledged ones are archived through the note archive (W28), all in ONE revision, recorded in a
``cleanup_runs`` row with a notice of what was archived. Acknowledged insights are left as they are (a person saw
them). Reversible: ``abstractnote unarchive --revision <revision_id>`` brings every one of them back in one revision.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .. import ids

KIND = "repeated_insights"


def _key(row: Any) -> Tuple[str, str, Tuple[str, ...], Tuple[str, ...]]:
    return (row["insight_kind"], ids.fold(row["title"] or ""), tuple(sorted(json.loads(row["evidence_note_ids_json"]
                                                                                   or "[]"))),
            tuple(sorted(json.loads(row["entity_ids_json"] or "[]"))))


def plan(nb: Any) -> Dict[str, Any]:
    """``{groups: [{kind, title, keep, archive: [note_id], count}], archive: [note_id], keep: [note_id], insights}``:
    the groups of repeated, live insights with more than one unacknowledged member (pure; oldest first)."""
    groups: Dict[tuple, List[Any]] = {}
    total = 0
    for r in nb.store.q("SELECT note_id, insight_kind, title, evidence_note_ids_json, entity_ids_json, received_at,"
                        " acked_at FROM notes WHERE insight_kind IS NOT NULL AND archived_at IS NULL"
                        " ORDER BY received_at, note_id"):
        total += 1
        if r["acked_at"] is None:
            groups.setdefault(_key(r), []).append(r)
    out: List[Dict[str, Any]] = []
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        out.append({"kind": key[0], "title": rows[0]["title"], "keep": rows[0]["note_id"],
                    "archive": [r["note_id"] for r in rows[1:]], "count": len(rows)})
    out.sort(key=lambda g: (-g["count"], g["kind"], ids.fold(g["title"] or "")))
    return {"insights": total, "groups": out, "archive": [n for g in out for n in g["archive"]],
            "keep": [g["keep"] for g in out]}


def notice(archived: int, kept: int) -> Optional[str]:
    """The end-user sentence of a cleanup (None when nothing was repeated)."""
    if not archived:
        return None
    return (f"Archived {archived} repeated insight{'s' if archived != 1 else ''} and kept the oldest of each"
            f" ({kept}); they can be brought back.")


def archive_repeated(nb: Any, *, dry_run: bool = False, account: Optional[str] = None, client: str = "cli"
                     ) -> Dict[str, Any]:
    """Archive the repeats :func:`plan` finds. ``dry_run`` writes nothing (a notebook opened without its embedder
    can answer it). Otherwise one ``cleanup_runs`` row (running, then completed or failed) and one revision through
    :meth:`Notebook.archive_notes`. Returns ``{dry_run, insights, groups, archived, kept, revision_id, cleanup,
    notice}`` (``groups`` lists every group with the notes it keeps and archives)."""
    p = plan(nb)
    out: Dict[str, Any] = {"dry_run": dry_run, "insights": p["insights"], "groups": p["groups"],
                           "archived": len(p["archive"]), "kept": len(p["keep"]), "revision_id": None,
                           "cleanup": None, "notice": notice(len(p["archive"]), len(p["keep"]))}
    if dry_run or not p["archive"]:
        if not dry_run:
            out["notice"] = "No repeated insights to archive."
        return out
    nb.require_embedder()
    cleanup_id = "cu_" + ids.sha256_text(f"{KIND}|{ids.utc_now()}|{'|'.join(p['archive'])}")[:24]
    with nb.store.tx() as c:
        c.execute("INSERT INTO cleanup_runs(cleanup_id, kind, started_at, status, total, groups) VALUES"
                  " (?,?,?,'running',?,?)", (cleanup_id, KIND, ids.utc_now(), len(p["archive"]), len(p["groups"])))
    try:
        res = nb.archive_notes(p["archive"], account, "a repeated insight", client=client,
                               target=f"insights:{cleanup_id}",
                               summary=f"Archived {len(p['archive'])} repeated insights (kept the oldest of each).")
    except Exception as exc:
        with nb.store.tx() as c:
            c.execute("UPDATE cleanup_runs SET status='failed', finished_at=?, error=? WHERE cleanup_id=?",
                      (ids.utc_now(), f"{type(exc).__name__}: {exc}", cleanup_id))
        raise
    with nb.store.tx() as c:
        c.execute("UPDATE cleanup_runs SET status='completed', finished_at=?, done=?, archived_json=?, kept_json=?,"
                  " revision_id=?, notice=? WHERE cleanup_id=?",
                  (ids.utc_now(), len(p["archive"]), json.dumps(p["archive"]), json.dumps(p["keep"]),
                   res["revision_id"], out["notice"], cleanup_id))
    out.update(revision_id=res["revision_id"], cleanup=cleanup_row(nb, cleanup_id))
    return out


def cleanup_row(nb: Any, cleanup_id: str) -> Optional[Dict[str, Any]]:
    """``{cleanup_id, kind, started_at, finished_at, status, total, done, groups, archived, kept, revision_id, notice,
    error}`` of one cleanup (``archived`` / ``kept``: note ids)."""
    r = nb.store.q1("SELECT * FROM cleanup_runs WHERE cleanup_id=?", (cleanup_id,))
    if r is None:
        return None
    return {"cleanup_id": r["cleanup_id"], "kind": r["kind"], "started_at": r["started_at"],
            "finished_at": r["finished_at"], "status": r["status"], "total": r["total"], "done": r["done"],
            "groups": r["groups"], "archived": json.loads(r["archived_json"] or "[]"),
            "kept": json.loads(r["kept_json"] or "[]"), "revision_id": r["revision_id"], "notice": r["notice"],
            "error": r["error"]}


def latest(nb: Any) -> Optional[Dict[str, Any]]:
    """The latest repeated-insights cleanup, or None."""
    r = nb.store.q1("SELECT cleanup_id FROM cleanup_runs WHERE kind=? ORDER BY started_at DESC, cleanup_id DESC"
                    " LIMIT 1", (KIND,))
    return cleanup_row(nb, r[0]) if r else None
