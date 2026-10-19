"""Recall on a copy of the corpus-evaluation notebook (backlog 0011, untracked/eval/REPORT.md).

The notebook is the framework's own history (3,987 notes, 146 entities, 219 claims). It lives under
``untracked/eval/notebook`` (not shipped); the tests copy it once per session and never write to the
original. Without it they are skipped with that reason. The query embedder is not available here, so
quick search runs on full text only (the warnings say so); the relevance floor must hold either way.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from abstractnote.store.notebook import Notebook

EVAL = Path(__file__).resolve().parents[1] / "untracked" / "eval" / "notebook"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "eval_quick_questions.json"

pytestmark = pytest.mark.skipif(not (EVAL / "note.sqlite3").is_file(),
                                reason="the corpus-evaluation notebook (untracked/eval/notebook) is not here")


@pytest.fixture(scope="session")
def eval_nb(tmp_path_factory):
    root = tmp_path_factory.mktemp("eval") / "notebook"
    shutil.copytree(EVAL, root)
    nb = Notebook.open(root)
    counts = nb.store.counts()
    assert counts["notes"] >= 3987 and counts["claims"] >= 219, counts
    yield nb
    nb.close()


def test_eval_contradiction_candidates_are_only_disputed_pairs(eval_nb):
    """REPORT section 2: the 11 candidates were all multi-valued predicates (mentions, hasPart, ...)."""
    items = eval_nb.contradictions(limit=200)["items"]
    disputed = eval_nb.store.q1("SELECT COUNT(*) FROM claims WHERE status='active' AND disputed_with_json!='[]'")[0]
    assert {i["rule"] for i in items} <= {"disputed_pair"}
    assert len(items) <= disputed  # the eval notebook has none
    assert items == []
