"""An agora hub database -> notes (docs/importers.md, recon data-sources.md section B).

One note per recorded decision (``store`` key ``decision:*``), per claim and work entry
(``claim:*``, ``work:*``), per resolved or answered thread (the root message plus its replies, in
order), and per operator message that is not already part of an imported thread.
``occurred_at`` is the message's ``created_at`` (a store entry takes its message's time when it
names one, else its ``updated_at``). The database is opened read-only.

Skipped as noise: system and file-change messages (``kind`` system/fs), the ``hub-alerts`` channel,
silence alerts, retracted messages. ``--channels`` keeps only the named channels.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from . import ImportReport, NoteDraft, in_window, iso_from_epoch

IMPORTER = "agora"
DEFAULT_OPERATORS = ("laurent", "dl-owner")
NOISE_CHANNELS = ("hub-alerts",)
STORE_PREFIXES = {"decision:": "decision", "claim:": "claim", "work:": "work"}


def _connect(db: Path) -> sqlite3.Connection:
    if not db.is_file():
        raise ValueError(f"There is no agora database at {db}.")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _data(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _source(row: sqlite3.Row, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    origin = {"message_id": row["id"], "channel": row["channel"], "seq": row["seq"], "sender": row["sender"],
              "reply_to": row["reply_to"], "status": row["status"], "hash": row["hash"]}
    origin.update(extra or {})
    return {"client": "importer", "importer": IMPORTER, "author": row["sender"],
            "origin": {k: v for k, v in origin.items() if v is not None}}


def _message_text(row: sqlite3.Row) -> str:
    title = str(row["title"] or "").strip()
    body = str(row["body"] or "").strip()
    return f"# {title}\n\n{body}" if title and body else (title or body)


def _attachments(conn: sqlite3.Connection, row: sqlite3.Row) -> List[Dict[str, Any]]:
    out = []
    for att in _data(row["data"]).get("attachments") or []:
        if not isinstance(att, dict) or not att.get("id"):
            continue
        blob = conn.execute("SELECT bytes, filename, content_type FROM blobs WHERE id=? LIMIT 1", (att["id"],)).fetchone()
        if blob is None or blob["bytes"] is None:
            continue
        out.append({"data": bytes(blob["bytes"]), "mime": att.get("content_type") or blob["content_type"],
                    "filename": att.get("filename") or blob["filename"]})
    return out


def _is_noise(row: sqlite3.Row, report: ImportReport, *, skip_system: bool) -> bool:
    if row["retracted_at"] is not None:
        report.skip("retracted")
        return True
    if skip_system and row["kind"] in ("system", "fs"):
        report.skip(f"kind_{row['kind']}")
        return True
    if skip_system and row["channel"] in NOISE_CHANNELS:
        report.skip("hub_alerts")
        return True
    if skip_system and _data(row["data"]).get("silence_alert"):
        report.skip("silence_alert")
        return True
    return False


def drafts(*, report: ImportReport, path: str | Path, channels: Optional[Sequence[str]] = None,
           skip_system: bool = True, operators: Sequence[str] = DEFAULT_OPERATORS,
           since: Optional[str] = None, until: Optional[str] = None) -> Iterator[NoteDraft]:
    conn = _connect(Path(path).expanduser())
    keep = {c.strip() for c in (channels or []) if c and c.strip()} or None
    try:
        yield from _store_drafts(conn, report, keep, since, until)
        yield from _thread_and_operator_drafts(conn, report, keep, skip_system, set(operators), since, until)
    finally:
        conn.close()


def _store_drafts(conn: sqlite3.Connection, report: ImportReport, keep: Optional[set], since: Optional[str],
                  until: Optional[str]) -> Iterator[NoteDraft]:
    rows = conn.execute(
        "SELECT channel, key, value, version, updated_by, updated_at FROM store "
        "WHERE key LIKE 'decision:%' OR key LIKE 'claim:%' OR key LIKE 'work:%' ORDER BY updated_at")
    for row in rows:
        if keep is not None and row["channel"] not in keep:
            report.skip("channel_filtered")
            continue
        kind = next(v for p, v in STORE_PREFIXES.items() if row["key"].startswith(p))
        try:
            value = json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            value = row["value"]
        message = None
        if isinstance(value, dict) and value.get("message_id"):
            message = conn.execute("SELECT * FROM messages WHERE id=?", (value["message_id"],)).fetchone()
        if isinstance(value, dict):
            summary = str(value.get("summary") or value.get("text") or value.get("title") or "").strip()
            details = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        else:
            summary, details = str(value or "").strip(), ""
        parts = [summary] if summary else []
        if message is not None:
            parts.append("## From the message\n\n" + _message_text(message))
        if details and not summary:
            parts.append(details)
        text = "\n\n".join(p for p in parts if p).strip()
        if not text:
            report.skip("empty")
            continue
        ts = iso_from_epoch(message["created_at"]) if message is not None else iso_from_epoch(row["updated_at"])
        if not in_window(ts, since, until):
            report.skip("outside_window")
            continue
        origin = {"channel": row["channel"], "store_key": row["key"], "version": row["version"],
                  "updated_by": row["updated_by"], "message_id": message["id"] if message is not None else None}
        source = {"client": "importer", "importer": IMPORTER, "author": row["updated_by"],
                  "origin": {k: v for k, v in origin.items() if v is not None}}
        yield NoteDraft(IMPORTER, f"store:{row['channel']}:{row['key']}:{row['version']}", text, ts, kind,
                        row["key"].split(":", 1)[1], source)


def _thread_and_operator_drafts(conn: sqlite3.Connection, report: ImportReport, keep: Optional[set],
                                skip_system: bool, operators: set, since: Optional[str], until: Optional[str]
                                ) -> Iterator[NoteDraft]:
    rows = conn.execute("SELECT * FROM messages ORDER BY created_at, seq").fetchall()
    by_id = {r["id"]: r for r in rows}

    def root_of(r: sqlite3.Row) -> sqlite3.Row:
        seen = set()
        while r["reply_to"] and r["reply_to"] in by_id and r["id"] not in seen:
            seen.add(r["id"])
            r = by_id[r["reply_to"]]
        return r

    replies: Dict[str, List[sqlite3.Row]] = {}
    kept: List[sqlite3.Row] = []
    for r in rows:
        if keep is not None and r["channel"] not in keep:
            report.skip("channel_filtered")
            continue
        if _is_noise(r, report, skip_system=skip_system):
            continue
        kept.append(r)
        if r["reply_to"]:
            replies.setdefault(root_of(r)["id"], []).append(r)
    in_thread: set = set()
    for r in kept:
        if r["reply_to"]:
            continue
        thread = replies.get(r["id"]) or []
        if not thread and r["status"] != "resolved":
            continue
        ts = iso_from_epoch(r["created_at"])
        in_thread.add(r["id"])
        in_thread.update(x["id"] for x in thread)
        if not in_window(ts, since, until):
            report.skip("outside_window")
            continue
        lines = [_message_text(r)]
        for x in thread:
            lines.append(f"## {x['sender']} ({iso_from_epoch(x['created_at'])})\n\n{_message_text(x)}")
        atts = _attachments(conn, r)
        for x in thread:
            atts.extend(_attachments(conn, x))
        yield NoteDraft(IMPORTER, f"thread:{r['id']}", "\n\n".join(lines), ts,
                        "resolved_thread" if r["status"] == "resolved" else "answered_thread",
                        str(r["title"] or "").strip() or None,
                        _source(r, extra={"replies": len(thread)}), atts)
    for r in kept:
        if r["sender"] not in operators or r["id"] in in_thread:
            continue
        text = _message_text(r)
        if not text:
            report.skip("empty")
            continue
        ts = iso_from_epoch(r["created_at"])
        if not in_window(ts, since, until):
            report.skip("outside_window")
            continue
        yield NoteDraft(IMPORTER, f"message:{r['id']}", text, ts, "operator_message",
                        str(r["title"] or "").strip() or None, _source(r), _attachments(conn, r))
