import Foundation
import Testing
@testable import AbstractGatewayClient

/// The decoders added for the Today themes, the entity page and quick recall, against the pinned
/// shapes of docs/api.md (no recording of these yet: to be refreshed from the hermetic gateway).
@Suite struct ThemesAndTimelineTests {
    private let d = JSONDecoder()

    @Test func communitiesReadLargestFirstWithUnnamedOnes() throws {
        let body = #"""
        {"computed_at": "2026-10-19T06:00:00Z", "resolution": 1.0, "revision_count_at_compute": 40, "revisions_since": 2, "loose_ends": 3,
         "items": [
          {"community_id": "cm_a", "label": "LUT budget", "summary": "The LUT project.", "kind": "project", "size": 9,
           "top_entities": [{"entity_id": "ex:person-dana", "name": "dana", "kind": "person", "note_count": 12}],
           "stale_label": false, "labelled_at": "2026-10-19T06:00:00Z", "created_at": "2026-10-18T06:00:00Z"},
          {"community_id": "cm_b", "label": null, "summary": null, "kind": null, "size": 3, "top_entities": [],
           "stale_label": false, "labelled_at": null, "created_at": "2026-10-19T06:00:00Z"}]}
        """#
        let c = try d.decode(Communities.self, from: Data(body.utf8))
        #expect(c.items.map(\.size) == [9, 3] && c.items[0].label == "LUT budget" && c.items[1].label == nil)
        #expect(c.items[0].topEntities.first?.name == "dana" && c.items[0].topEntities.first?.noteCount == 12)
        let empty = try d.decode(Communities.self, from: Data(#"{"computed_at": null, "items": []}"#.utf8))
        #expect(empty.computedAt == nil && empty.items.isEmpty)
        #expect(throws: (any Error).self) { try d.decode(Communities.self, from: Data(#"{"items": [{"community_id": "x"}]}"#.utf8)) }
    }

    @Test func anEntityCardCarriesItsCommunity() throws {
        let body = #"""
        {"entity_id": "ex:person-dana", "name": "dana", "display_name": "Dana", "kind": "person", "aliases": [], "summary": "Owns the budget.",
         "first_seen": "2026-09-02T09:00:00Z", "last_seen": "2026-10-17T16:40:00Z", "note_count": 12, "beliefs": [], "relations": [],
         "community": {"community_id": "cm_a", "label": "LUT budget", "kind": "project", "size": 9, "stale_label": false},
         "notes": [{"note_id": "n-12", "title": "LUT budget with Dana", "kind": "decision", "occurred_at": "2026-10-12T09:12:00Z", "excerpt": null}]}
        """#
        let card = try d.decode(EntityCard.self, from: Data(body.utf8))
        #expect(card.community?.label == "LUT budget" && card.community?.size == 9 && card.notes.count == 1)
        #expect(readableName("laurent_philippe_albou") == "laurent philippe albou")
    }

    @Test func aTimelineBecomesMilestonesWithChangesInPlainWords() throws {
        let body = #"""
        {"entity_id": "ex:person-dana", "since": null, "until": null, "items": [
          {"type": "note", "at": "2026-09-02T09:00:00Z", "note": {"note_id": "n-2", "title": "Kick-off", "kind": "event", "occurred_at": "2026-09-02T09:00:00Z", "excerpt": null}},
          {"type": "claim", "at": "2026-09-02T09:00:00Z", "claim": {"claim_id": "c1", "predicate": "schema:amount", "evidence": "The budget is 40k.", "note_id": "n-2"}},
          {"type": "supersede", "at": "2026-10-12T09:12:00Z", "claim_id": "c1", "note_id": "n-12", "reason": null, "superseded_by": "c2"},
          {"type": "merge", "at": "2026-10-13T09:00:00Z", "from_id": "a", "into_id": "b", "reason": null, "undone_at": null},
          {"type": "claim", "at": "2026-10-12T09:12:00Z", "claim": {"claim_id": "c2", "text": "the budget is 55k", "evidence": "Dana asked for 55k.", "note_id": "n-12"}}]}
        """#
        let t = try d.decode(EntityTimeline.self, from: Data(body.utf8))
        let m = t.milestones()
        #expect(m.map(\.kind) == [.noted, .learned, .changed, .learned])
        #expect(m[2].text == "The budget is 40k." && m[2].noteId == "n-12")   // what changed, in the note's words
        #expect(m[3].text == "the budget is 55k")                              // plain words preferred when given
        #expect(!m.contains { $0.text.contains("schema:") })
        // Beyond the limit: where it started and where it is now.
        let long = EntityTimeline(entityId: "e", items: (0..<10).map { i in
            TimelineItem(type: "note", at: "2026-01-\(String(format: "%02d", i + 1))T00:00:00Z",
                         note: NoteRef(noteId: "n\(i)", title: "N\(i)"), claim: nil, claimId: nil, noteId: nil) })
        #expect(long.milestones(limit: 5).map(\.text) == ["N0", "N1", "N7", "N8", "N9"])
    }

    @Test func citationMarkersLeaveTheAnswerText() {
        #expect(Citations.strip("You settled on 50k [nt_abc123] after Dana asked [nt_x_9].") == "You settled on 50k after Dana asked.")
        #expect(Citations.strip("No citation here [1].") == "No citation here [1].")
    }

    @Test func quickSearchAndNoteStatusRead() throws {
        let s = try d.decode(QuickSearch.self, from: Data(#"{"query": "lut", "order": "relevance", "notes": [{"note_id": "n-1", "title": "LUT", "kind": "idea", "occurred_at": "2026-10-01T00:00:00Z", "excerpt": "x", "score": 0.03, "relevance": 0.9}], "entities": [], "claims": [], "facets": {}, "considered": 4, "warnings": []}"#.utf8))
        #expect(s.notes.first?.noteId == "n-1")
        let n = try d.decode(NoteDetail.self, from: Data(#"{"note_id": "n", "title": "", "text": "t", "kind": null, "occurred_at": null, "status": "processing", "entities": []}"#.utf8))
        #expect(!n.isOrganized && !n.needsAttention && n.title.shown == nil)
    }
}
