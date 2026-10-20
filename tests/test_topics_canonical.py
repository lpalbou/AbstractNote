"""Topics stop fragmenting (eval2 D13): a topic is stored under its canonical name, a note keeps at most
TOPICS_PER_NOTE topics (the first the model lists), ``abstractnote normalize-topics`` merges the -/_/case and
plural variants already stored (one revision), and the extraction packet lists the top-50 existing topics."""

from __future__ import annotations

from abstractnote.process import packets
from abstractnote.store.notes import TOPICS_PER_NOTE
from test_store_support import nb, rev  # noqa: F401

AT = "2026-01-02T09:00:00Z"


def _assign(nb, rid, note_id, *topics):
    nb.apply_revision(rev(rid, AT, *({"op": "topic.assign", "payload": {"topic": t}} for t in topics), note_id=note_id))


def test_topics_are_canonical_and_capped_per_note(nb):
    n = nb.capture("about the gateway", occurred_at=AT).note_id
    _assign(nb, "rv_1", n, "Bug-Fix", "testing strategy", "Gateway", "fourth", "fifth")
    assert TOPICS_PER_NOTE == 3
    assert nb.store.note_topics(n) == ["bug_fix", "gateway", "testing_strategy"]
    _assign(nb, "rv_2", n, "another")  # a later revision cannot exceed the cap either
    assert len(nb.store.note_topics(n)) == 3
    _assign(nb, "rv_3", n, "GATEWAY")  # an existing one is still fine
    assert {t["topic"] for t in nb.topics()["items"]} == {"bug_fix", "gateway", "testing_strategy"}
    assert nb.search("", topics=["Bug-Fix"])["notes"][0]["note_id"] == n


def _legacy(nb, note_id, topic):  # a topic stored before canonical names
    with nb.store.tx() as c:
        c.execute("INSERT INTO note_topics(note_id, topic, assigned_by, revision_id, at) VALUES (?,?,?,?,?)",
                  (note_id, topic, "revision", None, AT))
        nb.store.refresh_topic(c, topic)


def test_normalize_topics_merges_spelling_and_plural_variants_in_one_revision(nb):
    a, b, c, d = (nb.capture(f"note {i}", occurred_at=AT).note_id for i in range(4))
    _legacy(nb, a, "bug-fix")
    _legacy(nb, b, "bug_fix")
    _legacy(nb, c, "bug_fixes")
    _legacy(nb, c, "Bug-Fix")
    _legacy(nb, d, "scifi-novel")
    _legacy(nb, d, "memory")
    plan = nb.normalize_topics(dry_run=True)
    groups = {g["into"]: sorted(g["from"]) for g in plan["groups"]}
    assert groups == {"bug_fix": ["Bug-Fix", "bug-fix", "bug_fixes"], "scifi_novel": ["scifi-novel"]}
    assert nb.store.note_topics(c) == ["Bug-Fix", "bug_fixes"]  # a dry run changes nothing
    out = nb.normalize_topics(account="me")
    assert out["revision_id"] and nb.revision(out["revision_id"])["source"]["account"] == "me"
    assert [nb.store.note_topics(x) for x in (a, b, c, d)] == [["bug_fix"], ["bug_fix"], ["bug_fix"],
                                                              ["memory", "scifi_novel"]]
    assert {t["topic"]: t["note_count"] for t in nb.topics()["items"]} == {"bug_fix": 3, "memory": 1,
                                                                           "scifi_novel": 1}
    assert nb.normalize_topics()["groups"] == []
    nb.rebuild()
    assert nb.store.note_topics(c) == ["bug_fix"]


def test_the_extraction_packet_lists_the_top_50_topics():
    assert packets.TOPICS == 50
    topics = [f"t{i}" for i in range(80)]
    p = packets.extraction_packet({"topics": []}, {"text": "x"}, 1, [], [], [], topics)
    line = next(ln for ln in p["known"].splitlines() if ln.startswith("Existing topics"))
    assert line.count(",") == 49 and "t49" in line and "t50" not in line
