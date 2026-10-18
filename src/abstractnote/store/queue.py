"""The processing queue (docs/cognition.md, "note-drain and the queue").

Rows live in the note index ``queue`` table. Order: priority desc, occurred_at
asc, size asc. A lease lasts 15 minutes; an expired lease is taken over by the
next drain and counts as a failed attempt. A failure schedules a retry after
1 minute, then 10 minutes; the third failure marks the note ``failed``
(``RETRY_BACKOFF_S[2]``, one hour, is the delay before the attempt that follows
a third failure when ``MAX_FAILURES`` is raised). Leasing runs in one
``BEGIN IMMEDIATE`` transaction, so two drains never lease the same note.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from .. import ids

LEASE_S = 15 * 60
RETRY_BACKOFF_S = (60, 10 * 60, 60 * 60)
MAX_FAILURES = 3
PRIORITY_BULK, PRIORITY_NORMAL, PRIORITY_HIGH, PRIORITY_URGENT = 0, 1, 2, 3
STOPPED_ERROR = "Processing stopped before it finished."


def _plus(ts: str, seconds: int) -> str:
    return (ids.parse_time(ts) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Queue:
    def __init__(self, store: Any) -> None:
        self.store = store

    # ------------------------------------------------------------------ writes

    def enqueue(self, conn: Any, note_id: str, priority: int, occurred_at: str, size: int, *,
                now: Optional[str] = None) -> None:
        """Add a note (inside the caller's transaction). Re-enqueueing keeps the existing row."""
        conn.execute(
            "INSERT OR IGNORE INTO queue(note_id, priority, occurred_at, size, enqueued_at, state)"
            " VALUES (?,?,?,?,?,'queued')", (note_id, int(priority), occurred_at, int(size), now or ids.utc_now()))
        conn.execute("UPDATE notes SET status='queued' WHERE note_id=? AND status='received'", (note_id,))

    def set_priority(self, conn: Any, note_id: str, priority: int) -> None:
        conn.execute("UPDATE queue SET priority=? WHERE note_id=?", (int(priority), note_id))

    def lease(self, worker_id: str, max_items: int = 1, *, now: Optional[str] = None) -> Dict[str, Any]:
        worker = str(worker_id or "").strip()
        if not worker:
            raise ValueError("A worker id is needed to take notes from the queue.")
        n = max(1, min(100, int(max_items or 1)))
        now = now or ids.utc_now()
        items: List[Dict[str, Any]] = []
        with self.store.tx() as c:
            # Expired leases: the drain stopped; that counts as a failure.
            for r in c.execute("SELECT note_id, failures FROM queue WHERE state='leased' AND lease_until<=?",
                               (now,)).fetchall():
                self._fail(c, r["note_id"], int(r["failures"]) + 1, STOPPED_ERROR, now)
            rows = c.execute(
                "SELECT note_id, priority, attempts FROM queue WHERE state='queued' AND"
                " (next_attempt_at IS NULL OR next_attempt_at<=?)"
                " ORDER BY priority DESC, occurred_at ASC, size ASC, note_id ASC LIMIT ?", (now, n)).fetchall()
            until = _plus(now, LEASE_S)
            for r in rows:
                c.execute("UPDATE queue SET state='leased', leased_by=?, lease_until=?, attempts=attempts+1"
                          " WHERE note_id=? AND state='queued'", (worker, until, r["note_id"]))
                c.execute("UPDATE notes SET status='processing' WHERE note_id=? AND archived_at IS NULL",
                          (r["note_id"],))
                items.append({"note_id": r["note_id"], "priority": r["priority"], "attempts": r["attempts"] + 1,
                              "lease_until": until})
        return {"items": items}

    def _fail(self, c: Any, note_id: str, failures: int, error: str, now: str) -> Dict[str, Any]:
        if failures >= MAX_FAILURES:
            c.execute("UPDATE queue SET state='failed', failures=?, last_error=?, failed_at=?, leased_by=NULL,"
                      " lease_until=NULL, next_attempt_at=NULL WHERE note_id=?", (failures, error, now, note_id))
            c.execute("UPDATE notes SET status='failed' WHERE note_id=? AND archived_at IS NULL", (note_id,))
            return {"note_id": note_id, "status": "failed"}
        nxt = _plus(now, RETRY_BACKOFF_S[min(failures - 1, len(RETRY_BACKOFF_S) - 1)])
        c.execute("UPDATE queue SET state='queued', failures=?, last_error=?, leased_by=NULL, lease_until=NULL,"
                  " next_attempt_at=? WHERE note_id=?", (failures, error, nxt, note_id))
        c.execute("UPDATE notes SET status='queued' WHERE note_id=? AND archived_at IS NULL", (note_id,))
        return {"note_id": note_id, "status": "queued", "next_attempt_at": nxt}

    def ack(self, note_id: str, outcome: str, error: Optional[str] = None, revision_id: Optional[str] = None, *,
            now: Optional[str] = None) -> Dict[str, Any]:
        if outcome not in ("ok", "error"):
            raise ValueError("The outcome must be ok or error.")
        now = now or ids.utc_now()
        with self.store.tx() as c:
            row = c.execute("SELECT * FROM queue WHERE note_id=?", (note_id,)).fetchone()
            if row is None:
                raise ValueError("That note is not in the queue.")
            if outcome == "ok":
                if revision_id is not None and c.execute(
                        "SELECT 1 FROM revisions WHERE revision_id=? AND note_id=? AND applied_index=1",
                        (revision_id, note_id)).fetchone() is None:
                    raise ValueError("That revision was not applied to this note.")
                c.execute("UPDATE queue SET state='done', done_at=?, leased_by=NULL, lease_until=NULL,"
                          " next_attempt_at=NULL WHERE note_id=?", (now, note_id))
                c.execute("UPDATE notes SET status='processed', processed_at=COALESCE(processed_at, ?)"
                          " WHERE note_id=? AND archived_at IS NULL", (now, note_id))
                return {"note_id": note_id, "status": "processed"}
            if row["state"] == "done":
                return {"note_id": note_id, "status": "processed"}
            return self._fail(c, note_id, int(row["failures"]) + 1, str(error or "Processing failed."), now)

    def retry(self, note_id: str, *, now: Optional[str] = None) -> Dict[str, Any]:
        """Re-queue a failed note (user action from the queue panel)."""
        with self.store.tx() as c:
            row = c.execute("SELECT state FROM queue WHERE note_id=?", (note_id,)).fetchone()
            if row is None:
                raise ValueError("That note is not in the queue.")
            if row["state"] != "failed":
                raise ValueError("Only a note that failed can be retried.")
            c.execute("UPDATE queue SET state='queued', failures=0, next_attempt_at=NULL, failed_at=NULL"
                      " WHERE note_id=?", (note_id,))
            c.execute("UPDATE notes SET status='queued' WHERE note_id=? AND archived_at IS NULL", (note_id,))
        return {"note_id": note_id, "status": "queued"}

    # ------------------------------------------------------------------ reads

    def attempt_for(self, note_id: str) -> int:
        """The attempt number a revision made now would carry (the current lease, or the next one)."""
        row = self.store.q1("SELECT state, attempts FROM queue WHERE note_id=?", (note_id,))
        if row is None:
            return 1
        return int(row["attempts"]) if row["state"] == "leased" and row["attempts"] else int(row["attempts"]) + 1

    def item(self, note_id: str) -> Optional[Dict[str, Any]]:
        row = self.store.q1("SELECT * FROM queue WHERE note_id=?", (note_id,))
        return dict(row) if row else None

    def stats(self) -> Dict[str, Any]:
        rows = self.store.q("SELECT state, priority, COUNT(*) AS n FROM queue GROUP BY state, priority")
        out: Dict[str, Any] = {"queued": 0, "processing": 0, "failed": 0, "done": 0,
                               "by_priority": {str(p): 0 for p in (3, 2, 1, 0)}}
        for r in rows:
            key = {"queued": "queued", "leased": "processing", "failed": "failed", "done": "done"}[r["state"]]
            out[key] += r["n"]
            if r["state"] in ("queued", "leased"):
                out["by_priority"][str(r["priority"])] = out["by_priority"].get(str(r["priority"]), 0) + r["n"]
        oldest = self.store.q1("SELECT MIN(enqueued_at) FROM queue WHERE state IN ('queued','leased')")
        out["oldest_enqueued_at"] = oldest[0] if oldest else None
        return out

    def summary(self, limit: int = 50) -> Dict[str, Any]:
        """Queue state and failed notes with their errors (``GET /note/queue``)."""
        failed = self.store.q(
            "SELECT q.note_id, q.last_error, q.failed_at, q.failures, n.title, n.occurred_at FROM queue q"
            " JOIN notes n ON n.note_id=q.note_id WHERE q.state='failed' ORDER BY q.failed_at DESC LIMIT ?",
            (int(limit),))
        waiting = self.store.q(
            "SELECT q.note_id, q.priority, q.state, q.attempts, q.next_attempt_at, n.title, n.occurred_at FROM queue q"
            " JOIN notes n ON n.note_id=q.note_id WHERE q.state IN ('queued','leased')"
            " ORDER BY q.priority DESC, q.occurred_at ASC, q.size ASC LIMIT ?", (int(limit),))
        return {"stats": self.stats(), "failed": [dict(r) for r in failed], "waiting": [dict(r) for r in waiting]}
