#!/usr/bin/env python3
"""A scripted OpenAI-compatible model server for the hermetic gateway (no weights, no network).

    python scripts/stub_llm.py --port 18852

Answers ``POST /v1/chat/completions`` with a JSON object valid for whichever AbstractNote schema
the request asks for (QuickFiling, Extraction, Linking, Answer, MiningPlan, SliceFindings,
Dossier, Reflections): the schema is recognised by its property names in the request
(``response_format`` or the prompt), and a plain chat request gets a short sentence.
``GET /v1/models`` lists one model, ``note-stub``. Development and smoke tests only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

MODEL = "note-stub"
DELAY_S = 0.0  # --delay: seconds per answer (lets a smoke test see a busy processor)

ANSWERS: Dict[str, Dict[str, Any]] = {
    "QuickFiling": {"title": "Dana and the LUT budget", "kind": "event", "topics": ["lut"], "urgency": "normal",
                    "language": "en", "one_line": "Met Dana about the LUT budget."},
    "Extraction": {
        "summary": "Met Dana about the LUT budget; she wants the Pareto by Friday.",
        "mentions": [{"ref": "m1", "name": "Dana", "kind": "person", "aliases": ["Dana"], "role": "asked for the Pareto"},
                     {"ref": "m2", "name": "LUT", "kind": "project", "aliases": ["LUT"], "role": "its budget"}],
        "claims": [{"subject": "m1", "predicate": "schema:participant", "object": "m2", "object_is_literal": False,
                    "certainty": "stated", "stance": "assert", "evidence": "Met Dana about the LUT budget",
                    "valid_from": None, "valid_until": None}],
        "tasks": [{"text": "Send Dana the Pareto by Friday", "status": "open", "due": None}],
        "time_refs": [], "topics": ["lut"],
    },
    "Linking": {
        "mentions": [{"ref": "m1", "decision": "create", "candidate": None, "merge_candidates": [], "name": "Dana",
                      "kind": "person", "aliases": ["Dana"], "summary": None, "reason": "first mention"},
                     {"ref": "m2", "decision": "create", "candidate": None, "merge_candidates": [], "name": "LUT",
                      "kind": "project", "aliases": ["LUT"], "summary": None, "reason": "first mention"}],
        "claims": [{"index": 0, "relation_to_existing": "new", "existing": None, "reason": "first statement"}],
        "tasks": [], "revision_summary": "Linked Dana to the LUT project.",
    },
    "Answer": {"answer": "You met Dana about the LUT budget; she wants the Pareto by Friday.", "evidence": [],
               "evolution": {"earliest": "", "latest": "", "turning_points": []}, "open_loops": [], "follow_ups": []},
    "MiningPlan": {"steps": [{"question": "Dana", "since": None, "until": None, "entities": []}],
                   "contradictions_to_test": [], "rounds": 1},
    "SliceFindings": {"facts": [], "changes": [], "gaps": []},
    "Dossier": {"title": "Dana and the LUT budget", "narrative": "Dana asked for the Pareto by Friday.", "timeline": [],
                "evolution": "", "contradictions": [], "forgotten": [], "suggestions": [], "confidence": "low"},
    "Reflections": {"items": []},
}
# Schema recognition by property names (ordered: most specific first).
SIGNATURES = [
    ("Linking", {"mentions", "claims", "tasks", "revision_summary"}),
    ("Extraction", {"summary", "mentions", "claims", "tasks", "time_refs", "topics"}),
    ("QuickFiling", {"title", "kind", "topics", "urgency", "language", "one_line"}),
    ("MiningPlan", {"steps", "contradictions_to_test", "rounds"}),
    ("SliceFindings", {"facts", "changes", "gaps"}),
    ("Dossier", {"narrative", "timeline", "forgotten", "confidence"}),
    ("Answer", {"answer", "evidence", "evolution", "open_loops", "follow_ups"}),
    ("Reflections", {"items"}),
]


def schema_for(body: Dict[str, Any]) -> Optional[str]:
    fmt = body.get("response_format") or {}
    props: set = set()
    schema = ((fmt.get("json_schema") or {}).get("schema") or {}) if isinstance(fmt, dict) else {}
    if isinstance(schema.get("properties"), dict):
        props = set(schema["properties"])
    for name, sig in SIGNATURES:
        if props and sig <= props and (name != "Reflections" or props == {"items"}):
            return name
    text = json.dumps(body.get("messages") or [])
    for name, sig in SIGNATURES:
        if all(f'\\"{p}\\"' in text or f'"{p}"' in text for p in sig) and name != "Reflections":
            return name
    return "Reflections" if '"items"' in text and "candidate" in text else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # one line per request on stderr
        sys.stderr.write("stub_llm %s\n" % (fmt % args))

    def _send(self, status: int, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "stub"}]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send(400, {"error": {"message": "bad json"}})
            return
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": "not found"}})
            return
        if DELAY_S:
            time.sleep(DELAY_S)
        name = schema_for(body)
        content = json.dumps(ANSWERS[name]) if name else "Noted."
        self._send(200, {
            "id": f"stub-{int(time.time() * 1000)}", "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model") or MODEL,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait before each answer")
    args = ap.parse_args()
    global DELAY_S
    DELAY_S = max(0.0, float(args.delay))
    if not 18800 <= args.port <= 18899:
        raise SystemExit("use a scratch port in 18800-18899")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
