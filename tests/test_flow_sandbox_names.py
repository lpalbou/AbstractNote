"""W32: every code node of the generated flows uses only names the runtime's code sandbox provides.

The sandbox (AbstractRuntime ``code_executor``: RestrictedPython's ``safe_builtins`` plus the helper globals) has no
``all`` or ``any``. In the Mnemosyne run (2026-10-21) the linking check ``link_valid`` called ``all(...)`` for a merge
decision: the NameError inside the linking loop ended that iteration, the runtime went on with the next part, and the
part's 15 decisions were dropped without a word (the note's mentions were created as new). MUTATION: put ``all(...)``
back in ``process/validate.py`` and rebuild the flows (this test goes red).
"""

from __future__ import annotations

import ast
import builtins
import json

from abstractnote.flows.runner import JSON_DIR


def _missing_names():
    from RestrictedPython import safe_builtins
    from abstractruntime.visualflow_compiler.visual.code_executor import sandbox_helper_globals

    allowed = set(safe_builtins) | set(sandbox_helper_globals())
    known = set(dir(builtins))
    out = []
    for path in sorted(JSON_DIR.glob("*.json")):
        for node in json.loads(path.read_text(encoding="utf-8")).get("nodes", []):
            body = (node.get("data") or {}).get("codeBody")
            if not body:
                continue
            tree = ast.parse("def transform(_input):\n" + "\n".join("    " + line for line in body.splitlines()))
            bad = sorted({x.id for x in ast.walk(tree) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
                          and x.id in known and x.id not in allowed})
            if bad:
                out.append((path.name, node.get("id"), bad))
    return out


def test_every_code_node_uses_only_names_the_sandbox_has():
    assert _missing_names() == []
