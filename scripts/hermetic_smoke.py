#!/usr/bin/env python3
"""Smoke test of AbstractNote through a hermetic gateway (scripts/hermetic_gateway.sh), recording fixtures.

    python scripts/hermetic_smoke.py --gateway http://127.0.0.1:18851 --token-a <alice> --token-b <bob> \
        --fixtures untracked/fixtures/gateway

What it checks (exit 1 on the first failure, with the reason):
  - anonymous calls answer 401; each account reads its own notebook;
  - an attachment uploads, a note-capture run with it completes with ``output.response`` and the same
    sentence appears as an ``answer_user`` step in the ledger, with ZERO tool-approval waits (S2);
  - a capture that names a local path (``{"path": "/etc/hosts"}``) fails with a plain sentence (B3);
  - an upload over the limit answers 413;
  - the processor automation is created, ``automation.run_now`` starts a drain, a second one while it
    runs answers 409 ``automation_busy``, and the queue drains;
  - a note-recall run started with ``prompt`` (the agent contract) answers on ``output.response`` (S3);
  - a retraction through the API closes the statement's window where it opens and is listed in the
    delta as ``kind: retracted`` in plain words (no vocabulary term);
  - the reflect automation has not run on its own (its first occurrence is one interval away), its
    "Reflect now" (``automation.run_now``) occurrence completes, every processor occurrence completed,
    and no run in the account's plane is ``failed`` (review 02 M1).

Every response it sees is written as a fixture ``<name>.json`` = ``{request, status, body}`` with
credentials removed, for the clients' tests (review 01 S5).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

FIXTURES: Optional[Path] = None
CURIE = re.compile(r"\b[a-z][a-z0-9]*:[A-Za-z]")  # a vocabulary term (schema:participant) in text a person reads
SCRUB: list = []  # (text, replacement): scratch paths left out of the recorded fixtures


class SmokeFailure(AssertionError):
    pass


def check(cond: bool, message: str) -> None:
    if not cond:
        raise SmokeFailure(message)


def _record(name: Optional[str], method: str, path: str, body: Any, status: int, payload: Any) -> None:
    if FIXTURES is None or not name:
        return
    FIXTURES.mkdir(parents=True, exist_ok=True)
    doc = {"request": {"method": method, "path": path, "body": body}, "status": status, "body": payload}
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    for needle, replacement in SCRUB:
        text = text.replace(needle, replacement)
    (FIXTURES / f"{name}.json").write_text(text, encoding="utf-8")


def call(gw: str, method: str, path: str, *, token: Optional[str] = None, json_body: Any = None,
         raw: Optional[bytes] = None, content_type: Optional[str] = None, fixture: Optional[str] = None,
         record_body: Any = None) -> Tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        data = raw
        headers["Content-Type"] = content_type or "application/octet-stream"
    req = urllib.request.Request(gw + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, text = resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode("utf-8")
    try:
        payload = json.loads(text) if text else None
    except json.JSONDecodeError:
        payload = text
    _record(fixture, method, path, json_body if record_body is None else record_body, status, payload)
    return status, payload


def multipart(fields: Dict[str, str], filename: str, content: bytes, mime: str) -> Tuple[bytes, str]:
    boundary = "----abstractnote" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode() + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def wait_run(gw: str, token: str, run_id: str, *, timeout_s: float = 120.0) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status, run = call(gw, "GET", f"/api/gateway/runs/{run_id}", token=token)
        check(status == 200, f"GET /runs/{run_id} answered {status}: {run}")
        if run.get("status") in ("completed", "failed", "cancelled"):
            return run
        check(not _approval_wait(run), f"run {run_id} is waiting for a tool approval: {run.get('waiting')}")
        time.sleep(0.5)
    raise SmokeFailure(f"run {run_id} did not finish within {timeout_s:.0f} s")


def _approval_wait(run: Dict[str, Any]) -> bool:
    waiting = run.get("waiting") or {}
    details = waiting.get("details") or {}
    return bool(details.get("tool_calls")) or "tool_approval" in str(waiting.get("wait_key") or "")


def wait_drained(gw: str, token: str, timeout_s: float = 180.0) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    stats: Dict[str, Any] = {}
    while time.time() < deadline:
        status, q = call(gw, "GET", "/api/gateway/note/queue", token=token)
        stats = (q or {}).get("stats") or {}
        if status == 200 and stats.get("queued", 1) == 0 and stats.get("processing", 1) == 0:
            _record("note_queue_200", "GET", "/api/gateway/note/queue", None, 200, q)
            return stats
        time.sleep(1)
    raise SmokeFailure(f"the queue did not drain within {timeout_s:.0f} s: {stats}")


def occurrences(gw: str, token: str, automation_id: str) -> list:
    status, body = call(gw, "GET", f"/api/gateway/automations/{automation_id}/occurrences?limit=100", token=token)
    check(status == 200, f"occurrences of {automation_id} answered {status}: {body}")
    return list(body.get("items") or [])


def wait_occurrence(gw: str, token: str, automation_id: str, seen: int, timeout_s: float = 300.0) -> Dict[str, Any]:
    """The newest occurrence once there are more than ``seen`` and it is finished."""
    deadline = time.time() + timeout_s
    items: list = []
    while time.time() < deadline:
        items = occurrences(gw, token, automation_id)
        if len(items) > seen and items[0].get("status") in ("completed", "failed", "cancelled"):
            return items[0]
        time.sleep(0.5)
    raise SmokeFailure(f"automation {automation_id} has no finished new occurrence after {timeout_s:.0f} s: {items[:1]}")


def run_now(gw: str, token: str, automation_id: str, fixture: Optional[str] = None) -> None:
    status = None
    for _ in range(60):
        status, out = call(gw, "POST", f"/api/gateway/automations/{automation_id}/commands", token=token,
                           json_body={"command_id": str(uuid.uuid4()), "type": "automation.run_now"})
        if status == 200:
            _record(fixture, "POST", f"/api/gateway/automations/{automation_id}/commands",
                    {"command_id": "<uuid>", "type": "automation.run_now"}, 200, out)
            return
        check(status == 409 and (out.get("detail") or {}).get("reason_code") == "automation_busy",
              f"run_now answered {status}: {out}")
        time.sleep(1)  # an occurrence is running: "already running", try again
    raise SmokeFailure(f"run_now of {automation_id} never got through ({status})")


def failed_runs(gw: str, token: str) -> list:
    status, body = call(gw, "GET", "/api/gateway/runs?status=failed&limit=1000&include_ledger_len=false", token=token)
    check(status == 200, f"GET /runs?status=failed answered {status}: {body}")
    return [{k: r.get(k) for k in ("run_id", "workflow_id", "parent_run_id")} for r in body.get("items") or []]


def ledger(gw: str, token: str, run_id: str) -> list:
    status, body = call(gw, "GET", f"/api/gateway/runs/{run_id}/ledger?limit=100000", token=token)
    check(status == 200, f"ledger of {run_id} answered {status}")
    return list(body.get("items") or [])


def start_run(gw: str, token: str, flow_id: str, input_data: Dict[str, Any], session_id: str,
              fixture: Optional[str] = None) -> str:
    body = {"bundle_id": "abstractnote", "flow_id": flow_id, "session_id": session_id, "input_data": input_data}
    status, out = call(gw, "POST", "/api/gateway/runs/start", token=token, json_body=body, fixture=fixture)
    check(status == 200 and out.get("run_id"), f"runs/start {flow_id} answered {status}: {out}")
    return str(out["run_id"])


def main() -> int:
    global FIXTURES
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--token-a", required=True, help="first account (alice)")
    ap.add_argument("--token-b", required=True, help="second account (bob)")
    ap.add_argument("--fixtures", type=Path, default=None, help="directory for recorded fixtures")
    ap.add_argument("--max-attachment-bytes", type=int, default=1048576)
    ap.add_argument("--scrub", action="append", default=[], help="a path prefix replaced by <scratch> in fixtures")
    args = ap.parse_args()
    SCRUB.extend((p, "<scratch>") for p in sorted(args.scrub, key=len, reverse=True) if p)
    gw, a, b = args.gateway.rstrip("/"), args.token_a, args.token_b
    if gw.endswith(":8080"):
        raise SystemExit("never against :8080")
    FIXTURES = args.fixtures
    session = "s-smoke-" + uuid.uuid4().hex[:8]
    report: Dict[str, Any] = {}

    # 401 and isolation (before anything is written)
    status, _ = call(gw, "GET", "/api/gateway/note/status", fixture="note_status_401")
    check(status == 401, f"anonymous status answered {status}")
    status, before_b = call(gw, "GET", "/api/gateway/note/status", token=b, fixture="note_status_empty_200")
    check(status == 200, f"status (bob) answered {status}")

    # attachment upload and the 413 case
    raw, ctype = multipart({"session_id": session}, "budget.txt", b"LUT budget: 120k, Pareto due Friday.\n", "text/plain")
    status, up = call(gw, "POST", "/api/gateway/attachments/upload", token=a, raw=raw, content_type=ctype,
                      fixture="attachments_upload_200", record_body={"multipart": {"session_id": session, "file": "budget.txt"}})
    check(status == 200 and (up.get("artifact") or {}).get("$artifact"), f"upload answered {status}: {up}")
    big = b"x" * (args.max_attachment_bytes + 1)
    raw_big, ctype_big = multipart({"session_id": session}, "big.bin", big, "application/octet-stream")
    status, _ = call(gw, "POST", "/api/gateway/attachments/upload", token=a, raw=raw_big, content_type=ctype_big,
                     fixture="attachments_upload_413", record_body={"multipart": {"session_id": session, "file": f"big.bin ({len(big)} bytes)"}})
    check(status == 413, f"an upload over the limit answered {status}")

    # capture with the attachment
    key = str(uuid.uuid4())
    run_id = start_run(gw, a, "note-capture", {
        "text": "Met Dana about the LUT budget; she wants the Pareto by Friday.",
        "occurred_at": "2026-10-19T09:12:00Z", "source": {"client": "watch"}, "idempotency_key": key,
        "session_id": session, "context": {"attachments": [up["artifact"]]},
    }, session, fixture="runs_start_capture_200")
    run = wait_run(gw, a, run_id)
    _record("run_capture_completed_200", "GET", f"/api/gateway/runs/{run_id}", None, 200, run)
    out = run.get("output") or {}
    check(run.get("status") == "completed" and out.get("success") is not False, f"capture run ended {run.get('status')}: {out}")
    check(str(out.get("response") or "").startswith("Saved as "), f"capture output.response is {out.get('response')!r}")
    note_id = str(out.get("note_id") or "")
    steps = ledger(gw, a, run_id)
    answers = [((s.get("effect") or {}).get("payload") or {}).get("message") for s in steps
               if (s.get("effect") or {}).get("type") == "answer_user" and s.get("status") == "completed"]
    check(out["response"] in answers, f"the ledger has no answer_user step with the response ({answers})")
    approval_waits = [s for s in steps if s.get("status") == "waiting" and "tool" in json.dumps(s.get("result") or {})]
    check(not approval_waits, f"the capture parked on tool approval: {approval_waits[:1]}")
    report["capture"] = {"run_id": run_id, "note_id": note_id, "response": out["response"], "ledger_steps": len(steps),
                         "approval_waits": 0}

    # the same idempotency key again: duplicate
    dup_id = start_run(gw, a, "note-capture", {"text": "Met Dana about the LUT budget; she wants the Pareto by Friday.",
                                               "idempotency_key": key, "session_id": session}, session)
    dup = wait_run(gw, a, dup_id)
    _record("run_capture_duplicate_200", "GET", f"/api/gateway/runs/{dup_id}", None, 200, dup)
    check((dup.get("output") or {}).get("status") == "duplicate", f"a retried capture answered {dup.get('output')}")

    # B3: a local path is refused with a plain sentence
    bad_id = start_run(gw, a, "note-capture", {"text": "read this", "session_id": session,
                                               "context": {"attachments": [{"path": "/etc/hosts"}]}}, session)
    bad = wait_run(gw, a, bad_id)
    _record("run_capture_path_refused", "GET", f"/api/gateway/runs/{bad_id}", None, 200, bad)
    bad_out = bad.get("output") or {}
    check(bad_out.get("success") is False and "upload" in str(bad_out.get("error") or "").lower(),
          f"a capture naming /etc/hosts was not refused plainly: {bad_out}")
    report["path_refused"] = bad_out.get("error")

    # status: alice has the note, bob has none
    status, st_a = call(gw, "GET", "/api/gateway/note/status", token=a, fixture="note_status_200")
    status_b, st_b = call(gw, "GET", "/api/gateway/note/status", token=b)
    check(status == 200 and st_a["counts"]["notes"] >= 1, f"alice's status: {status} {st_a}")
    check(status_b == 200 and st_b["counts"]["notes"] == before_b["counts"]["notes"], "bob's notebook changed")
    status, _ = call(gw, "GET", f"/api/gateway/note/notes/{note_id}", token=b, fixture="note_get_other_account_404")
    check(status == 404, f"bob reading alice's note answered {status}")

    # processor automation, run_now and 409 automation_busy
    status, ens = call(gw, "POST", "/api/gateway/note/processor/ensure", token=a, fixture="note_processor_ensure_200")
    check(status == 200 and ens.get("processor_id"), f"processor ensure answered {status}: {ens}")
    proc = ens["processor_id"]
    status, again = call(gw, "POST", "/api/gateway/note/processor/ensure", token=a)
    check(status == 200 and again.get("processor_id") == proc and again.get("created") == [], "ensure is not idempotent")
    # The schedule's first tick starts a drain at once; wait for it to empty the queue.
    stats = wait_drained(gw, a)
    check(stats.get("failed", 0) == 0 and stats.get("done", 0) >= 1, f"the first drain did not process the note: {stats}")
    # A second note, then "Process now": 200, and a repeat while it runs is 409 automation_busy.
    second = start_run(gw, a, "note-capture", {"text": "Dana moved the Pareto deadline to next week.",
                                               "idempotency_key": str(uuid.uuid4()), "session_id": session}, session)
    wait_run(gw, a, second)
    run_now(gw, a, proc, fixture="automation_run_now_200")
    busy_status = None
    for _ in range(40):
        busy_status, busy = call(gw, "POST", f"/api/gateway/automations/{proc}/commands", token=a,
                                 json_body={"command_id": str(uuid.uuid4()), "type": "automation.run_now"})
        if busy_status == 409:
            _record("automation_run_now_409_busy", "POST", f"/api/gateway/automations/{proc}/commands",
                    {"command_id": "<uuid>", "type": "automation.run_now"}, 409, busy)
            break
        time.sleep(0.25)
    check(busy_status == 409, f"a second run_now while draining answered {busy_status}")
    stats = wait_drained(gw, a)
    check(stats.get("failed", 0) == 0 and stats.get("done", 0) >= 2, f"the queue did not drain cleanly: {stats}")
    report["queue"] = stats
    # a user retraction: a revision with the account as source, listed in the delta in plain words (M3, m3, m4)
    status, before = call(gw, "GET", "/api/gateway/note/changes", token=a)
    check(status == 200, f"changes answered {status}")
    status, note = call(gw, "GET", f"/api/gateway/note/notes/{note_id}", token=a)
    claims = [c for c in (note or {}).get("claims") or [] if c.get("status") == "active"]
    check(status == 200 and claims, f"the filed note has no active statement to retract: {status} {note}")
    cid = claims[0]["claim_id"]
    status, ret = call(gw, "POST", f"/api/gateway/note/claims/{cid}/retract", token=a,
                       json_body={"reason": "Dana was never on that project."}, fixture="note_claim_retract_200")
    check(status == 200 and ret.get("revision_id"), f"retract answered {status}: {ret}")
    status, cl = call(gw, "GET", f"/api/gateway/note/claims/{cid}", token=a)
    check(status == 200, f"GET /note/claims/{cid} answered {status}: {cl}")
    claim_row = cl["claim"]
    check(claim_row.get("status") == "retracted" and claim_row.get("valid_until") == claim_row.get("valid_from"),
          f"a retracted statement keeps a window: {claim_row}")
    status, delta = call(gw, "GET", f"/api/gateway/note/changes?since={before['cursor']}", token=a)
    gone = [c for c in delta.get("superseded_claims") or [] if c.get("claim_id") == cid]
    check(gone and gone[0].get("kind") == "retracted" and gone[0].get("superseded_by") is None,
          f"the retraction is not listed as retracted: {delta.get('superseded_claims')}")
    check(bool(gone[0].get("text")) and not CURIE.search(gone[0]["text"]),
          f"the delta names a vocabulary term: {gone[0].get('text')!r}")
    report["retraction"] = gone[0]
    for name, path in (("note_get_200", f"/api/gateway/note/notes/{note_id}"), ("note_changes_200", "/api/gateway/note/changes"),
                       ("note_overview_200", "/api/gateway/note/overview"), ("note_cues_200", "/api/gateway/note/cues"),
                       ("note_tasks_200", "/api/gateway/note/tasks"), ("note_insights_200", "/api/gateway/note/insights"),
                       ("note_entities_200", "/api/gateway/note/entities"), ("note_graph_200", "/api/gateway/note/graph"),
                       ("note_settings_200", "/api/gateway/note/settings")):
        status, body = call(gw, "GET", path, token=a, fixture=name)
        report.setdefault("reads", {})[path] = status

    # recall with `prompt` (agent contract): output.response
    rid = start_run(gw, a, "note-recall", {"prompt": "What did Dana ask for?"}, session, fixture="runs_start_recall_200")
    rec = wait_run(gw, a, rid)
    _record("run_recall_completed_200", "GET", f"/api/gateway/runs/{rid}", None, 200, rec)
    check(str((rec.get("output") or {}).get("response") or "").strip(), f"recall output.response is empty: {rec.get('output')}")
    report["recall_response"] = rec["output"]["response"]

    # reflect: nothing on its own at creation; "Reflect now" completes; no failed run anywhere (M1)
    reflect = ens["reflect_id"]
    seen = len(occurrences(gw, a, reflect))
    check(seen == 0 or "note-reflect" not in ens.get("created", []),
          "the reflect automation ran at creation (its first run is one interval away)")
    run_now(gw, a, reflect, fixture="automation_reflect_run_now_200")
    occ = wait_occurrence(gw, a, reflect, seen=seen)
    check(occ.get("status") == "completed", f"the reflect occurrence ended {occ.get('status')}: {occ.get('failure')}")
    drains = occurrences(gw, a, proc)
    check(drains and all(o.get("status") == "completed" for o in drains),
          f"a processor occurrence did not complete: {[(o.get('index'), o.get('status'), o.get('failure')) for o in drains]}")
    for who, token in (("alice", a), ("bob", b)):
        failed = failed_runs(gw, token)
        check(not failed, f"{who}'s plane has failed runs: {failed}")
    report["automations"] = {"reflect": occ.get("status"), "processor_occurrences": len(drains), "failed_runs": 0}
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as e:
        print(f"SMOKE FAILED: {e}", file=sys.stderr)
        raise SystemExit(1)
