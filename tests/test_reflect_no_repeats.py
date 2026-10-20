"""Reflect stops repeating itself (eval2 D5): a candidate already backed by an unacknowledged insight is left out,
ages count from the last mention, approximate dates (a commit, a file date) never age, a run writes at most
REFLECT_PER_KIND insights of one kind, and a second reflect run writes no duplicate."""

from __future__ import annotations

from abstractnote.flows.build import REFLECT_PER_KIND
from abstractnote.recall import reflect
from abstractnote.store.notebook import Notebook
from test_store_support import nb, rev  # noqa: F401

NOW = "2026-10-20T12:00:00Z"


def _task_note(nb, i, occurred, *, basis=None):
    kw = {}
    if basis:
        kw = {"importer": "markdown", "source_key": f"repo:doc{i}.md:{i}",
              "source": {"origin": {"path": f"doc{i}.md", "date_basis": basis}}}
    note_id = nb.capture(f"Roadmap item {i}: ship part {i}.", occurred_at=occurred, **kw).note_id
    nb.apply_revision(rev(f"rv_{i}", occurred, {"op": "task.open", "payload": {"task_id": f"tk_{i}",
                                                                               "text": f"Ship part {i}"}},
                          note_id=note_id))
    return note_id


def test_approximate_dates_never_age_and_ages_count_from_the_last_mention(nb):
    old = [_task_note(nb, i, f"2025-0{i + 1}-01T09:00:00Z") for i in range(3)]
    approx = _task_note(nb, 9, "2025-05-26T10:00:00Z", basis="git_added")
    tasks = {t["task_id"]: t for t in nb.tasks(now=NOW)["items"]}
    assert tasks["tk_9"]["stale"] is False and tasks["tk_9"]["age_days"] > reflect.STALE_DAYS
    assert tasks["tk_0"]["stale"] is True
    loops = reflect.open_loops(nb, now=NOW)["items"]
    assert {it["task_id"] for it in loops} == {"tk_0", "tk_1", "tk_2"}
    # a recent note revisits tk_1's note: its last mention is recent, so it is not old any more
    recent = nb.capture("Back on part 1 this week.", occurred_at="2026-10-15T09:00:00Z").note_id
    with nb.store.tx() as c:
        c.execute("INSERT INTO note_links(src_note_id, dst_note_id, predicate, score, matched_by, created_at)"
                  " VALUES (?,?,'ex:revisits',1.0,'test',?)", (recent, old[1], NOW))
    loops = {it["task_id"]: it for it in reflect.open_loops(nb, now=NOW)["items"]}
    assert set(loops) == {"tk_0", "tk_2"}
    assert loops["tk_0"]["last_mentioned_at"] == "2025-01-01T09:00:00Z" and loops["tk_0"]["stale"] is True
    assert approx not in {n for it in loops.values() for n in it["note_ids"]}


def test_an_unacknowledged_insight_hides_its_candidate_and_a_reworded_copy_is_a_duplicate(nb):
    _task_note(nb, 0, "2025-01-01T09:00:00Z")
    [c] = reflect.open_loops(nb, now=NOW)["items"]
    first = nb.write_insight("reminder", "Part 0", "Still open.", c["note_ids"], c["entity_ids"])
    assert first["status"] == "new"
    assert reflect.open_loops(nb, now=NOW)["items"] == []
    again = nb.write_insight("reminder", "Part 0 is 600 days old", "Reworded.", c["note_ids"], c["entity_ids"])
    assert again == {"note_id": first["note_id"], "status": "duplicate", "warnings": []}
    nb.ack_insight(first["note_id"])
    assert [it["task_id"] for it in reflect.open_loops(nb, now=NOW)["items"]] == ["tk_0"]
    assert nb.write_insight("reminder", "Part 0 is 600 days old", "Reworded.", c["note_ids"],
                            c["entity_ids"])["status"] == "new"


def test_a_second_reflect_run_writes_no_duplicate_and_each_run_caps_a_kind(tmp_path):
    from abstractnote.flows.runner import NoteRunner, ScriptedLLM
    from abstractnote.tools import bind

    path = tmp_path / "nb"
    with Notebook.open(path) as seeded:
        for i in range(5):
            _task_note(seeded, i, f"2025-0{i + 1}-01T09:00:00Z")
        _task_note(seeded, 9, "2025-05-26T10:00:00Z", basis="git_added")
    picks = [{"candidate": i, "kind": "reminder", "title": f"Part {i}", "body": "Still open."} for i in range(5)]
    picks.append({"candidate": 0, "kind": "warning", "title": "Again", "body": "The same candidate twice."})
    script = {"Reflections": {"items": picks}}
    runner = NoteRunner.with_llm(ScriptedLLM(script), bind(path))
    first = runner.run_reflect(limit=10)
    assert first["considered"] == 5 and len(first["insight_note_ids"]) == REFLECT_PER_KIND
    second = runner.run_reflect(limit=10)
    assert second["considered"] == 5 - REFLECT_PER_KIND
    assert len(second["insight_note_ids"]) == 5 - REFLECT_PER_KIND
    third = runner.run_reflect(limit=10)
    assert third["considered"] == 0 and third["insight_note_ids"] == []
    with Notebook.open(path) as nb2:
        rows = nb2.store.q("SELECT evidence_note_ids_json FROM notes WHERE insight_kind IS NOT NULL")
        assert len(rows) == 5 and len({r[0] for r in rows}) == 5
