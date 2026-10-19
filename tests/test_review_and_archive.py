"""W10 store and recall gaps: processor throughput settings, the review list (unsure matches, merges made by the
model, disagreements) and its confirm action, merged entries read as themselves, and the archive snapshot axis
(``as_received``: what the archive held on a date, excluding later imports)."""

from __future__ import annotations

import pytest

from test_store_support import nb  # noqa: F401


def test_processor_schedule_defaults_follow_the_drain_automation_and_are_checked(nb):
    from abstractnote.flows.build import AUTOMATION_DEFAULTS, DRAIN_DEFAULTS

    sched = nb.settings()["processor_schedule"]
    assert sched == {"every": AUTOMATION_DEFAULTS["note-drain"]["trigger"]["config"]["every"],
                     "max_items": DRAIN_DEFAULTS["max_items"]}
    assert nb.processor_throughput() == {"max_items": sched["max_items"], "interval": sched["every"]}
    nb.update_settings({"processor_schedule": {"every": "90s", "max_items": 5}})
    assert nb.processor_throughput() == {"max_items": 5, "interval": "90s"}
    for bad in ({"every": "2 minutes", "max_items": 5}, {"every": "2m", "max_items": 0},
                {"every": "2m", "max_items": True}, {"every": "2m", "colour": 1}, "2m"):
        with pytest.raises(ValueError):
            nb.update_settings({"processor_schedule": bad})
    # once the processor automation exists, the throughput it was created with is what runs
    nb.set_meta("processor_automation_id", "au_x")
    nb.set_meta("processor_schedule_applied", {"every": "3m", "max_items": 9})
    nb.update_settings({"processor_schedule": {"every": "1h", "max_items": 50}})
    assert nb.processor_throughput() == {"max_items": 9, "interval": "3m"}
