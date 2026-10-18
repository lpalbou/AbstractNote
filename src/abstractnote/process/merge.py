"""Merge partial Extractions of a multi-section note before linking.

Mentions are unioned by exact case-folded name and kind (refs renumbered m1,
m2, ...; aliases unioned); claims, tasks and time references are concatenated
with their refs remapped; topics are unioned; summaries are joined. This is
the same rule as the flow's merge step.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple


def merge_extractions(parts: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Returns ``(merged extraction, mention list for note_resolve_candidates)``."""
    parts = [p for p in (parts or []) if isinstance(p, dict)]
    if not parts:
        raise ValueError("There is no extraction to merge.")
    if len(parts) == 1:
        merged = dict(parts[0])
    else:
        mentions: List[Dict[str, Any]] = []
        refmap: Dict[Tuple[int, Any], str] = {}
        by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
        claims: List[Dict[str, Any]] = []
        tasks: List[Dict[str, Any]] = []
        time_refs: List[Dict[str, Any]] = []
        topics: List[str] = []
        summaries: List[str] = []
        for i, part in enumerate(parts):
            for m in part.get("mentions") or []:
                key = (str(m.get("name") or "").casefold(), str(m.get("kind") or ""))
                if key in by_key:
                    kept = by_key[key]
                    for a in m.get("aliases") or []:
                        if a not in kept["aliases"]:
                            kept["aliases"].append(a)
                else:
                    kept = dict(m, ref="m" + str(len(mentions) + 1), aliases=list(m.get("aliases") or []))
                    by_key[key] = kept
                    mentions.append(kept)
                refmap[(i, m.get("ref"))] = kept["ref"]
            for c in part.get("claims") or []:
                c2 = dict(c)
                c2["subject"] = refmap.get((i, c.get("subject")), c.get("subject"))
                if not c.get("object_is_literal"):
                    c2["object"] = refmap.get((i, c.get("object")), c.get("object"))
                claims.append(c2)
            tasks.extend(part.get("tasks") or [])
            time_refs.extend(part.get("time_refs") or [])
            for t in part.get("topics") or []:
                if t not in topics:
                    topics.append(t)
            if part.get("summary"):
                summaries.append(str(part["summary"]))
        merged = {"summary": " ".join(summaries), "mentions": mentions, "claims": claims, "tasks": tasks,
                  "time_refs": time_refs, "topics": topics}
    names = [{"ref": m.get("ref"), "name": m.get("name"), "kind": m.get("kind"), "aliases": m.get("aliases") or []}
             for m in merged.get("mentions") or []]
    return merged, names
