"""Every model call has explicit settings, and an extraction that names nothing is read again
(backlog 0024 step 1). Each check goes red when the setting or the retry is removed."""

from __future__ import annotations

import pytest

from abstractnote.flows import build
from abstractnote.flows.runner import NoteRunner, ScriptedLLM

STEPS = {("note-capture", "filing"): "quick_filing", ("note-process", "extract"): "extract",
         ("note-process", "extract_again"): "extract", ("note-process", "link"): "link",
         ("note-recall", "synthesize"): "synthesize", ("note-mine", "plan"): "mine_plan",
         ("note-mine", "findings"): "mine_findings", ("note-mine", "dossier"): "mine_dossier",
         ("note-reflect", "reflect"): "reflect", ("note-reflect", "communities_names"): "label_communities",
         ("note-consolidate", "communities_names"): "label_communities",
         ("note-consolidate", "summaries"): "entity_summaries"}


def _llm_nodes():
    flows = build.build_all()
    return flows, {(fid, n["id"]): n for fid, f in flows.items() for n in f["nodes"]
                   if n["data"]["nodeType"] == "llm_call"}


def test_every_model_node_pins_thinking_and_an_output_budget():
    _, llm = _llm_nodes()
    assert set(llm) == set(STEPS)
    for key, step in STEPS.items():
        d = llm[key]["data"]["pinDefaults"]
        assert 0 < d["max_output_tokens"] == build.STEP_SETTINGS[step]["max_output_tokens"], key
        assert d.get("thinking") == build.STEP_SETTINGS[step]["thinking"], key
    pins = {k[1]: (llm[k]["data"]["pinDefaults"].get("thinking"), llm[k]["data"]["pinDefaults"]["max_output_tokens"])
            for k in llm if k[0] in ("note-capture", "note-process")}
    assert pins == {"filing": ("off", 400), "extract": ("on", 4000), "extract_again": ("on", 4000),
                    "link": ("on", 6000)}


def test_sections_are_bounded_for_the_extract_budget():
    flows, _ = _llm_nodes()
    args = next(n for n in flows["note-process"]["nodes"] if n["id"] == "context_args")
    assert args["data"]["pinDefaults"]["max_chars"] == build.SECTION_CHARS == 6000


EMPTY = {"summary": "A note.", "mentions": [], "claims": [], "tasks": [], "time_refs": [], "topics": []}
FULL = {"summary": "Met Dana.", "mentions": [{"ref": "m1", "name": "Dana", "kind": "person", "aliases": [],
                                               "role": "met"}],
        "claims": [], "tasks": [], "time_refs": [], "topics": []}
LINK = {"mentions": [{"ref": "m1", "decision": "create", "candidate": None, "merge_candidates": [], "name": "Dana",
                      "kind": "person", "aliases": [], "summary": "Dana, met about the budget.", "reason": "new"}],
        "claims": [], "tasks": [], "revision_summary": "Met Dana."}
FILING = {"title": "t", "kind": "event", "topics": [], "urgency": "normal", "language": "en", "one_line": "x"}


def _runner(tmp_path, extractions):
    from abstractnote.tools import bind

    llm = ScriptedLLM({"QuickFiling": FILING, "Extraction": list(extractions), "Linking": [LINK, LINK]})
    return NoteRunner.with_llm(llm, bind(tmp_path / "nb")), llm


def test_an_empty_extraction_of_a_text_is_read_once_more(tmp_path):
    runner, llm = _runner(tmp_path, [EMPTY, FULL])
    runner.run_capture("Met Dana about the LUT budget; she wants the Pareto by Friday.")
    out = runner.run_drain(max_items=5, budget_s=60)
    assert out["processed"] == 1
    assert [c["schema"] for c in llm.calls].count("Extraction") == 2
    from abstractnote import Notebook

    nb = Notebook.open(tmp_path / "nb")
    assert nb.store.counts()["entities"] == 1
    nb.close()


def test_a_second_empty_reading_is_kept_and_flagged(tmp_path):
    runner, llm = _runner(tmp_path, [EMPTY, EMPTY])
    runner.run_capture("Met Dana about the LUT budget; she wants the Pareto by Friday.")
    runner.run_drain(max_items=5, budget_s=60)
    from abstractnote import Notebook

    nb = Notebook.open(tmp_path / "nb")
    (row,) = nb.store.q("SELECT warnings_json FROM revisions WHERE abandoned=0 AND action IS NULL")
    assert "A part of this note names nothing although it has text, even when read a second time." in row[0]
    nb.close()


def test_the_node_thinking_holds_whatever_the_run_asks(tmp_path):
    runner, llm = _runner(tmp_path, [FULL])
    seen = []
    real = llm.generate

    def spy(**kw):
        seen.append(((kw.get("params") or {}).get("thinking"), (kw.get("params") or {}).get("max_output_tokens")))
        return real(**kw)

    llm.generate = spy
    runner.run_capture("Met Dana.", _runtime={"thinking": True})
    runner.run_drain(max_items=5, budget_s=60, _runtime={"thinking": True})
    # the drain's own thinking choice (off by default, the bulk-import setting) wins over the run's _runtime
    assert seen == [("off", 400), ("off", 4000), ("off", 6000)]


@pytest.mark.parametrize("asked, expected", [("on", "on"), ("off", "off"), ("", "on"), (" ON ", "on")])
def test_thinking_is_a_per_run_choice_of_the_drain_and_the_process_flow(tmp_path, asked, expected):
    runner, llm = _runner(tmp_path, [FULL])
    seen = []
    real = llm.generate

    def spy(**kw):
        seen.append((kw.get("params") or {}).get("thinking"))
        return real(**kw)

    llm.generate = spy
    note_id = runner.run_capture("Met Dana.")["note_id"]
    if asked == " ON ":
        runner.run("note-process", {"note_id": note_id, "thinking": asked})  # note-process itself defaults to on
    else:
        runner.run_drain(max_items=5, budget_s=60, thinking=asked if asked else "on")
    assert seen[1:] == [expected, expected]


def test_inline_processing_at_capture_keeps_thinking_on(tmp_path):
    runner, llm = _runner(tmp_path, [FULL])
    seen = []
    real = llm.generate

    def spy(**kw):
        seen.append((kw.get("params") or {}).get("thinking"))
        return real(**kw)

    llm.generate = spy
    runner.run_capture("Met Dana.", process_inline=True)
    assert seen == ["off", "on", "on"]


def test_an_unknown_thinking_value_is_refused(tmp_path):
    from abstractnote.flows.runner import FlowRunError

    runner, _ = _runner(tmp_path, [FULL])
    note_id = runner.run_capture("Met Dana.")["note_id"]
    with pytest.raises(FlowRunError, match="Thinking must be on or off"):
        runner.run("note-process", {"note_id": note_id, "thinking": "low"})
