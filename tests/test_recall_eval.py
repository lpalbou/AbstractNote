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


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("item", _fixture()["questions"], ids=lambda i: i["q"][:40])
def test_eval_quick_questions_return_no_irrelevant_entity_or_statement_rows(eval_nb, item):
    """REPORT section 2: entity and claim results were irrelevant for all 15 quick questions."""
    out = eval_nb.search(item["q"], limit=50)
    assert [e["entity_id"] for e in out["entities"]] == item["entities"]
    assert [c["claim_id"] for c in out["claims"]] == item["claims"]
    if item["expect_note_id"]:
        ids = [n["note_id"] for n in out["notes"]]
        assert item["expect_note_id"] in ids[: item["within"]], (item["expect_title"], ids[:5])


@pytest.mark.parametrize("probe", _fixture()["probes"], ids=lambda p: p["q"])
def test_eval_relevant_entities_are_grouped_under_their_note(eval_nb, probe):
    """The floor keeps what is relevant: a question about a processed note returns its entity, under that note."""
    from abstractnote.recall.search import HIT_MIN_SCORE

    out = eval_nb.search(probe["q"], limit=20)
    returned = {n["note_id"]: n for n in out["notes"]}
    assert probe["note_id"] in returned
    got = {e["entity_id"]: e for e in out["entities"]}
    for eid in probe["entities"]:
        assert eid in got and probe["note_id"] in got[eid]["note_ids"]
        assert eid in returned[probe["note_id"]]["matched_entities"]
        assert got[eid]["first_at"] <= got[eid]["latest_at"]
    for e in out["entities"]:
        assert e["score"] >= HIT_MIN_SCORE and set(e["note_ids"]) <= set(returned)
    for c in out["claims"]:
        assert c["score"] >= HIT_MIN_SCORE and set(c["note_ids"]) <= set(returned)


@pytest.mark.parametrize("probe", _fixture()["earliest"], ids=lambda p: p["q"])
def test_eval_earliest_first_lists_the_mentions_in_history_order(eval_nb, probe):
    """REPORT section 2: no earliest-first answer. The oracle is the full-text index itself: every note
    containing all the subject's words, oldest first."""
    out = eval_nb.search(probe["q"], limit=10, order="earliest")
    match = " AND ".join(f'"{w}"' for w in probe["all_words"])
    oracle = [r[0] for r in eval_nb.store.q(
        "SELECT n.note_id FROM notes_fts f JOIN notes n ON n.note_id=f.note_id WHERE notes_fts MATCH ? AND"
        " n.archived_at IS NULL ORDER BY n.occurred_at, n.note_id LIMIT 10", (match,))]
    assert out["order"] == "earliest"
    assert [n["note_id"] for n in out["notes"]] == oracle
    dates = [n["occurred_at"] for n in out["notes"]]
    assert dates == sorted(dates)
    relevance = eval_nb.search(probe["q"], limit=10)
    assert [n["note_id"] for n in relevance["notes"]] != oracle  # the default order is not history order


@pytest.mark.parametrize("with_query", [True, False])
def test_eval_degenerate_standard_packet_stays_under_budget(eval_nb, with_query):
    """REPORT 4.3 item 7: standard recall passed 80-167k characters; note_expand alone gave 144k here."""
    from abstractnote.recall.expand import EXPAND_BUDGET_CHARS

    case = _fixture()["standard_packet"]
    out = eval_nb.expand(case["note_ids"], case["entity_ids"], query=case["question"] if with_query else None)
    size = len(json.dumps(out, default=str))
    assert size <= EXPAND_BUDGET_CHARS == 24_000 and out["used_chars"] == size
    assert size < case["before_chars"] / 4
    assert out["notes"] and out["notes"][0]["note_id"] == case["note_ids"][0]  # best scored first
    assert all(isinstance(e["line"], str) and "\n" not in e["line"] for e in out["entities"])
    focus = {eval_nb.store.canonical(e) for e in case["entity_ids"]}
    assert {t["entity_id"] for t in out["claims_timeline"]} <= focus
    hub = [t for t in out["claims_timeline"] if t["entity_id"] == "ex:software-agora-hub"]
    assert hub and hub[0]["claims"]
