"""The contract between the flows and the prompt files under ``abstractnote/process/prompts/``.

Each model step has a static system prompt (``<name>.system.md``) and a user-message template
(``<name>.md``) rendered INSIDE the flow by a ``string_template`` node with ``{{var}}`` /
``{{var.key}}`` placeholders. ``FLOW_PROMPT_VARS`` names the variables each flow wires into each
user template; the generator refuses a template that uses a placeholder the flow does not
provide, and the tests check both sides agree. (The module keeps its historical name; it no
longer carries fallback prompt texts.)
"""

from __future__ import annotations

from typing import Dict, FrozenSet

#: The variables each flow wires into each user template (the contract with the prompt files).
FLOW_PROMPT_VARS: Dict[str, FrozenSet[str]] = {
    "quick_filing": frozenset({"packet"}),
    "extract": frozenset({"packet"}),
    "link": frozenset({"batch"}),
    "synthesize": frozenset({"question", "as_of", "hits", "expansion"}),
    "mine_plan": frozenset({"question", "since", "until", "hits"}),
    "mine_findings": frozenset({"question", "step", "hits"}),
    "mine_dossier": frozenset({"question", "plan", "findings"}),
    "reflect": frozenset({"candidates", "limit"}),
}
