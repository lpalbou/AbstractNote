import Foundation

// AbstractNote wire models (abstractnote docs/api.md). Written against the documented shapes on
// 2026-10-19, before the note router existed; the fixture tests (Tests/.../Fixtures and
// NoteFixtureTests) say which field names were assumed. Required fields are decoded strictly, so
// a renamed field fails loudly instead of showing an empty screen.

/// Where a note was written, when the person allowed it.
public struct CaptureLocation: Codable, Hashable, Sendable {
    public var lat: Double
    public var lon: Double
    public var label: String?
    public init(lat: Double, lon: Double, label: String? = nil) { self.lat = lat; self.lon = lon; self.label = label }
}

/// What a note was written about (an insight, an open loop, a cue), sent as `context.about`.
public struct NoteSubject: Codable, Hashable, Sendable {
    public var kind: String
    public var id: String
    public var title: String
    public init(kind: String, id: String, title: String) { self.kind = kind; self.id = id; self.title = title }
}

/// One capture as the person sent it. The idempotency key is made when Send is tapped and reused
/// on every retry of that same note, so a radio retry never makes a second note.
public struct Capture: Hashable, Sendable {
    public var text: String
    public var occurredAt: Date
    public var location: CaptureLocation?
    public var idempotencyKey: String
    public var sessionId: String
    public var about: NoteSubject?
    /// Uploaded files kept with the note (a recording: "Send as recording"), passed unchanged as
    /// `context.attachments`.
    public var attachments: [JSONValue]
    public init(text: String, occurredAt: Date, location: CaptureLocation?, idempotencyKey: String, sessionId: String,
                about: NoteSubject? = nil, attachments: [JSONValue] = []) {
        self.text = text; self.occurredAt = occurredAt; self.location = location
        self.idempotencyKey = idempotencyKey; self.sessionId = sessionId; self.about = about; self.attachments = attachments
    }
}

public enum NoteFlows {
    public static let bundleId = "abstractnote"
    public static let capture = "note-capture"
    public static let recall = "note-recall"
    /// `note-watch-<uuid>`: the watch's own conversation id for its captures and questions.
    public static func newSessionId() -> String { "note-watch-" + UUID().uuidString.lowercased() }
}

/// `POST /api/gateway/runs/start` for `note-capture` (api.md "Starting flows").
struct CaptureStart: Encodable {
    struct Source: Encodable { var client = "watch" }
    struct Context: Encodable {
        var about: NoteSubject?
        var attachments: [JSONValue]?
    }
    struct Input: Encodable {
        var text: String
        var occurred_at: String
        var location: CaptureLocation?
        var source = Source()
        var idempotency_key: String
        var process_inline = false
        /// The gateway does not hand the request's `session_id` to the flow: it goes here too.
        var session_id: String
        var context: Context?
    }
    var bundle_id = NoteFlows.bundleId
    var flow_id = NoteFlows.capture
    var session_id: String
    var input_data: Input

    init(_ c: Capture) {
        session_id = c.sessionId
        input_data = Input(text: c.text, occurred_at: ISO8601.string(c.occurredAt), location: c.location,
                           idempotency_key: c.idempotencyKey, session_id: c.sessionId,
                           context: c.about == nil && c.attachments.isEmpty ? nil : Context(about: c.about, attachments: c.attachments.isEmpty ? nil : c.attachments))
    }
}

/// `POST /api/gateway/runs/start` for `note-recall`.
struct RecallStart: Encodable {
    /// `session_id` repeats the request's: the gateway does not hand it to the flow.
    struct Input: Encodable { var question: String; var effort: String; var session_id: String }
    var bundle_id = NoteFlows.bundleId
    var flow_id = NoteFlows.recall
    var session_id: String
    var input_data: Input
}

public enum RecallEffort: String, Sendable, Codable { case quick, standard, deep }

/// `note-capture`'s run output: the filing sentence for the person, and the note it made.
public struct CaptureAnswer: Codable, Hashable, Sendable {
    public var response: String
    public var noteId: String?
    /// `new` or `duplicate` (the same idempotency key was already saved).
    public var status: String?
    enum CodingKeys: String, CodingKey { case response, noteId = "note_id", status }
    public init(response: String, noteId: String?, status: String?) { self.response = response; self.noteId = noteId; self.status = status }
}

/// A note an answer rests on.
public struct Evidence: Codable, Hashable, Sendable, Identifiable {
    public var noteId: String
    public var title: String?
    public var occurredAt: String?
    public var excerpt: String?
    public var id: String { noteId }
    enum CodingKeys: String, CodingKey { case noteId = "note_id", title, occurredAt = "occurred_at", excerpt }
    public init(noteId: String, title: String?, occurredAt: String? = nil, excerpt: String? = nil) {
        self.noteId = noteId; self.title = title; self.occurredAt = occurredAt; self.excerpt = excerpt
    }
}

/// `note-recall`'s run output (api.md "Ask a question"); the watch reads the answer and its evidence.
public struct RecallAnswer: Codable, Hashable, Sendable {
    public var response: String
    public var evidence: [Evidence]
    public var dossierNoteId: String?
    enum CodingKeys: String, CodingKey { case response, evidence, dossierNoteId = "dossier_note_id" }
    public init(response: String, evidence: [Evidence], dossierNoteId: String? = nil) {
        self.response = response; self.evidence = evidence; self.dossierNoteId = dossierNoteId
    }
}

/// Where a started flow stands.
public enum RunProgress<Answer: Sendable & Hashable>: Sendable, Hashable {
    case working
    /// Stopped on a person: an approval or a question the watch cannot answer.
    case needsPerson
    case done(Answer)
    /// The run failed or the flow refused the input; the sentence to show when the run gave one
    /// (`output.error` or the run's `error`, without the runtime's "Runtime error: " prefix).
    case failed(String?)
}

/// `{items, next_cursor}`: every note API list.
public struct ListPage<Item: Decodable & Sendable>: Decodable, Sendable {
    public var items: [Item]
    public var nextCursor: String?
    enum CodingKeys: String, CodingKey { case items, nextCursor = "next_cursor" }
}

/// A note in a list.
public struct NoteRef: Codable, Hashable, Sendable, Identifiable {
    public var noteId: String
    public var title: String?
    public var kind: String?
    public var occurredAt: String?
    public var excerpt: String?
    public var id: String { noteId }
    enum CodingKeys: String, CodingKey { case noteId = "note_id", title, kind, occurredAt = "occurred_at", excerpt }
    public init(noteId: String, title: String?, kind: String? = nil, occurredAt: String? = nil, excerpt: String? = nil) {
        self.noteId = noteId; self.title = title; self.kind = kind; self.occurredAt = occurredAt; self.excerpt = excerpt
    }
}

/// A person, project, place or idea the notebook knows.
public struct EntityRef: Codable, Hashable, Sendable, Identifiable {
    public var entityId: String
    public var name: String
    public var kind: String?
    public var id: String { entityId }
    enum CodingKeys: String, CodingKey { case entityId = "entity_id", name, kind }
    public init(entityId: String, name: String, kind: String? = nil) { self.entityId = entityId; self.name = name; self.kind = kind }
}

/// A belief that a newer note replaced.
/// A claim that stopped being believed (`GET /note/changes` `superseded_claims[]`): `kind` is
/// `superseded` (a later note replaced it) or `retracted` (withdrawn); `text` is plain words.
public struct ClaimChange: Codable, Hashable, Sendable, Identifiable {
    public var claimId: String
    public var kind: String?
    public var text: String?
    public var supersededBy: String?
    public var id: String { claimId }
    public var isRetraction: Bool { kind == "retracted" }
    enum CodingKeys: String, CodingKey { case claimId = "claim_id", kind, text, supersededBy = "superseded_by" }
    public init(claimId: String, kind: String? = "superseded", text: String?, supersededBy: String? = nil) {
        self.claimId = claimId; self.kind = kind; self.text = text; self.supersededBy = supersededBy
    }
}

/// A finding of the reflect flow or a deep answer (a note of kind `insight` or `synthesis`).
public struct Insight: Codable, Hashable, Sendable, Identifiable {
    public var noteId: String
    public var title: String
    public var body: String?
    public var insightKind: String?
    public var occurredAt: String?
    public var ackedAt: String?
    public var id: String { noteId }
    enum CodingKeys: String, CodingKey {
        case noteId = "note_id", title, body, insightKind = "insight_kind", occurredAt = "occurred_at", ackedAt = "acked_at"
    }
    public init(noteId: String, title: String, body: String? = nil, insightKind: String? = nil, occurredAt: String? = nil, ackedAt: String? = nil) {
        self.noteId = noteId; self.title = title; self.body = body; self.insightKind = insightKind; self.occurredAt = occurredAt; self.ackedAt = ackedAt
    }
}

/// An open loop: something said to be done that no note has closed yet.
public struct OpenLoop: Codable, Hashable, Sendable, Identifiable {
    public var taskId: String
    public var noteId: String?
    public var text: String
    public var status: String
    public var due: String?
    public var id: String { taskId }
    enum CodingKeys: String, CodingKey { case taskId = "task_id", noteId = "note_id", text, status, due }
    public init(taskId: String, noteId: String? = nil, text: String, status: String = "open", due: String? = nil) {
        self.taskId = taskId; self.noteId = noteId; self.text = text; self.status = status; self.due = due
    }
}

/// A cue card: a prompt answered by adding a note. `subject` is what it is about (an entity, a
/// loop, …) when the reflect flow named one; `evidence_note_ids` are the notes behind it (the "Why").
public struct Cue: Codable, Hashable, Sendable, Identifiable {
    public var cueId: String
    public var text: String
    public var kind: String?
    public var subject: NoteSubject?
    public var evidenceNoteIds: [String]
    public var entityIds: [String]
    public var id: String { cueId }
    enum CodingKeys: String, CodingKey {
        case cueId = "cue_id", text, kind, subject, evidenceNoteIds = "evidence_note_ids", entityIds = "entity_ids"
    }
    public init(cueId: String, text: String, kind: String? = nil, subject: NoteSubject? = nil, evidenceNoteIds: [String] = [], entityIds: [String] = []) {
        self.cueId = cueId; self.text = text; self.kind = kind; self.subject = subject; self.evidenceNoteIds = evidenceNoteIds; self.entityIds = entityIds
    }
}

/// `GET /note/changes?since=`: what the notebook learned since the cursor, and the cursor to keep
/// for the next visit. Absent lists are empty; the cursor is required.
public struct NoteChanges: Decodable, Hashable, Sendable {
    public var cursor: String
    public var notes: [NoteRef]
    public var entities: [EntityRef]
    public var supersededClaims: [ClaimChange]
    public var closedLoops: [OpenLoop]
    public var failedNotes: [NoteRef]
    public var insights: [Insight]
    enum CodingKeys: String, CodingKey {
        case cursor, notes, entities, supersededClaims = "superseded_claims", closedLoops = "closed_loops", failedNotes = "failed_notes", insights
    }
    public init(cursor: String, notes: [NoteRef] = [], entities: [EntityRef] = [], supersededClaims: [ClaimChange] = [],
                closedLoops: [OpenLoop] = [], failedNotes: [NoteRef] = [], insights: [Insight] = []) {
        self.cursor = cursor; self.notes = notes; self.entities = entities; self.supersededClaims = supersededClaims
        self.closedLoops = closedLoops; self.failedNotes = failedNotes; self.insights = insights
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        cursor = try c.decode(String.self, forKey: .cursor)
        notes = try c.decodeIfPresent([NoteRef].self, forKey: .notes) ?? []
        entities = try c.decodeIfPresent([EntityRef].self, forKey: .entities) ?? []
        supersededClaims = try c.decodeIfPresent([ClaimChange].self, forKey: .supersededClaims) ?? []
        closedLoops = try c.decodeIfPresent([OpenLoop].self, forKey: .closedLoops) ?? []
        failedNotes = try c.decodeIfPresent([NoteRef].self, forKey: .failedNotes) ?? []
        insights = try c.decodeIfPresent([Insight].self, forKey: .insights) ?? []
    }
    public var isEmpty: Bool {
        notes.isEmpty && entities.isEmpty && supersededClaims.isEmpty && closedLoops.isEmpty && failedNotes.isEmpty && insights.isEmpty
    }
}

/// `GET /note/overview`: the watch reads only today's resurfacing ("on this day").
public struct NoteOverview: Decodable, Hashable, Sendable {
    public var resurfacing: [NoteRef]
    enum CodingKeys: String, CodingKey { case resurfacing }
    public init(resurfacing: [NoteRef]) { self.resurfacing = resurfacing }
}

/// `GET /note/notes/{id}` `insight`: what makes a note an insight (null on other notes).
public struct InsightFacts: Codable, Hashable, Sendable {
    public var kind: String?
    public var evidenceNoteIds: [String]
    public var entityIds: [String]
    public var ackedAt: String?
    enum CodingKeys: String, CodingKey { case kind, evidenceNoteIds = "evidence_note_ids", entityIds = "entity_ids", ackedAt = "acked_at" }
    public init(kind: String?, evidenceNoteIds: [String], entityIds: [String] = [], ackedAt: String? = nil) {
        self.kind = kind; self.evidenceNoteIds = evidenceNoteIds; self.entityIds = entityIds; self.ackedAt = ackedAt
    }
}

/// `GET /note/notes/{id}`: the watch shows a note's text, when it was written, its kind, the entities
/// it is linked to and whether it has been understood (`status`: `queued`, `processing`,
/// `processed`, `failed`, …), and for an insight the notes behind it. Every other key is ignored.
public struct NoteDetail: Decodable, Hashable, Sendable, Identifiable {
    public var noteId: String
    public var title: String?
    public var text: String
    public var kind: String?
    public var occurredAt: String?
    public var status: String
    public var entities: [EntityBrief]
    public var insight: InsightFacts?
    public var id: String { noteId }
    enum CodingKeys: String, CodingKey { case noteId = "note_id", title, text, kind, occurredAt = "occurred_at", status, entities, insight }
    public init(noteId: String, title: String?, text: String, kind: String? = nil, occurredAt: String? = nil, status: String = "processed",
                entities: [EntityBrief] = [], insight: InsightFacts? = nil) {
        self.noteId = noteId; self.title = title; self.text = text; self.kind = kind; self.occurredAt = occurredAt
        self.status = status; self.entities = entities; self.insight = insight
    }
    /// Understood by the processor: its entities and links are in.
    public var isOrganized: Bool { status == "processed" }
    /// The processor gave up on it (the queue keeps it for a retry).
    public var needsAttention: Bool { status == "failed" }
}

/// An entity as a note or a list carries it. `name` is the canonical name the API says clients show.
public struct EntityBrief: Codable, Hashable, Sendable, Identifiable {
    public var entityId: String
    public var name: String
    public var kind: String?
    public var id: String { entityId }
    enum CodingKeys: String, CodingKey { case entityId = "entity_id", name, kind }
    public init(entityId: String, name: String, kind: String? = nil) { self.entityId = entityId; self.name = name; self.kind = kind }
}

/// The community an entity card carries: `{community_id, label, kind, size, stale_label}`.
public struct CommunityRef: Decodable, Hashable, Sendable {
    public var communityId: String
    public var label: String?
    public var kind: String?
    public var size: Int
    enum CodingKeys: String, CodingKey { case communityId = "community_id", label, kind, size }
    public init(communityId: String, label: String?, kind: String? = nil, size: Int) {
        self.communityId = communityId; self.label = label; self.kind = kind; self.size = size
    }
}

/// A community member listed on the community (its 5 with the most notes).
public struct CommunityMember: Decodable, Hashable, Sendable, Identifiable {
    public var entityId: String
    public var name: String
    public var kind: String?
    public var noteCount: Int?
    public var id: String { entityId }
    enum CodingKeys: String, CodingKey { case entityId = "entity_id", name, kind, noteCount = "note_count" }
    public init(entityId: String, name: String, kind: String? = nil, noteCount: Int? = nil) {
        self.entityId = entityId; self.name = name; self.kind = kind; self.noteCount = noteCount
    }
}

/// A theme: a group of entities the notes keep together, named by the reflect flow (`label` and
/// `summary` are null until it is named).
public struct Community: Decodable, Hashable, Sendable, Identifiable {
    public var communityId: String
    public var label: String?
    public var summary: String?
    public var kind: String?
    public var size: Int
    public var topEntities: [CommunityMember]
    public var id: String { communityId }
    enum CodingKeys: String, CodingKey { case communityId = "community_id", label, summary, kind, size, topEntities = "top_entities" }
    public init(communityId: String, label: String?, summary: String? = nil, kind: String? = nil, size: Int, topEntities: [CommunityMember]) {
        self.communityId = communityId; self.label = label; self.summary = summary; self.kind = kind; self.size = size; self.topEntities = topEntities
    }
}

/// `GET /note/communities`: largest first; `computed_at` null and no items before the first computation.
public struct Communities: Decodable, Hashable, Sendable {
    public var computedAt: String?
    public var items: [Community]
    enum CodingKeys: String, CodingKey { case computedAt = "computed_at", items }
    public init(computedAt: String?, items: [Community]) { self.computedAt = computedAt; self.items = items }
}

/// `GET /note/entities/{id}`: the card the watch shows.
public struct EntityCard: Decodable, Hashable, Sendable, Identifiable {
    public var entityId: String
    public var name: String
    public var kind: String?
    public var summary: String?
    public var firstSeen: String?
    public var lastSeen: String?
    public var noteCount: Int?
    public var community: CommunityRef?
    public var notes: [NoteRef]
    public var id: String { entityId }
    enum CodingKeys: String, CodingKey {
        case entityId = "entity_id", name, kind, summary, firstSeen = "first_seen", lastSeen = "last_seen", noteCount = "note_count", community, notes
    }
    public init(entityId: String, name: String, kind: String?, summary: String?, firstSeen: String?, lastSeen: String?,
                noteCount: Int?, community: CommunityRef?, notes: [NoteRef]) {
        self.entityId = entityId; self.name = name; self.kind = kind; self.summary = summary; self.firstSeen = firstSeen
        self.lastSeen = lastSeen; self.noteCount = noteCount; self.community = community; self.notes = notes
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        entityId = try c.decode(String.self, forKey: .entityId)
        name = try c.decode(String.self, forKey: .name)
        kind = try c.decodeIfPresent(String.self, forKey: .kind)
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        firstSeen = try c.decodeIfPresent(String.self, forKey: .firstSeen)
        lastSeen = try c.decodeIfPresent(String.self, forKey: .lastSeen)
        noteCount = try c.decodeIfPresent(Int.self, forKey: .noteCount)
        community = try c.decodeIfPresent(CommunityRef.self, forKey: .community)   // null before the first computation
        notes = try c.decode([NoteRef].self, forKey: .notes)
    }
}

/// A statement in an entity's timeline (the fields the watch reads).
public struct TimelineClaim: Decodable, Hashable, Sendable {
    public var claimId: String
    /// Plain words when the server gives them; else the note's words that support it (`evidence`).
    public var text: String?
    public var evidence: String?
    public var noteId: String?
    enum CodingKeys: String, CodingKey { case claimId = "claim_id", text, evidence, noteId = "note_id" }
    public var words: String? { text.shown ?? evidence.shown }
}

/// One step of `GET /note/entities/{id}/timeline`: `type` `note`, `claim`, `supersede`, `retract` or `merge`.
public struct TimelineItem: Decodable, Hashable, Sendable {
    public var type: String
    public var at: String?
    public var note: NoteRef?
    public var claim: TimelineClaim?
    public var claimId: String?
    public var noteId: String?
    enum CodingKeys: String, CodingKey { case type, at, note, claim, claimId = "claim_id", noteId = "note_id" }
}

/// `GET /note/entities/{id}/timeline`: claims and notes in history order.
public struct EntityTimeline: Decodable, Hashable, Sendable {
    public var entityId: String
    public var items: [TimelineItem]
    enum CodingKeys: String, CodingKey { case entityId = "entity_id", items }

    /// A milestone of "How it evolved": when, what kind of step, its words, the note to open.
    public struct Milestone: Hashable, Sendable, Identifiable {
        public enum Kind: String, Sendable { case noted, learned, changed }
        public var at: String?
        public var kind: Kind
        public var text: String
        public var noteId: String?
        public var id: String { "\(at ?? "")|\(kind.rawValue)|\(text)" }
    }

    /// The steps worth showing on a watch, oldest first: notes, statements learned, statements that
    /// changed (superseded or retracted); merges are bookkeeping and left out. Beyond `limit`, the
    /// first two and the latest ones are kept (where it started, where it is now).
    public func milestones(limit: Int = 6) -> [Milestone] {
        var said: [String: String] = [:]
        var out: [Milestone] = []
        for i in items {
            switch i.type {
            case "note":
                guard let n = i.note else { continue }
                out.append(Milestone(at: i.at, kind: .noted, text: n.title.shown ?? n.excerpt.shown ?? "A note", noteId: n.noteId))
            case "claim":
                guard let c = i.claim, let w = c.words else { continue }
                said[c.claimId] = w
                out.append(Milestone(at: i.at, kind: .learned, text: w, noteId: c.noteId))
            case "supersede", "retract":
                guard let id = i.claimId, let w = said[id] else { continue }
                out.append(Milestone(at: i.at, kind: .changed, text: w, noteId: i.noteId))
            default: continue
            }
        }
        guard out.count > limit, limit >= 3 else { return out }
        return Array(out.prefix(2)) + Array(out.suffix(limit - 2))
    }
}

/// `GET /note/search?q=`: the quick tier (no model). The watch reads the notes, best first.
public struct QuickSearch: Decodable, Hashable, Sendable {
    public var notes: [NoteRef]
    enum CodingKeys: String, CodingKey { case notes }
}

extension Optional where Wrapped == String {
    /// A title to show: nil when absent or blank (the gateway sends "" for an untitled note).
    public var shown: String? {
        guard let s = self?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
        return s
    }
}

/// What `POST /note/processor/ensure` did to one automation: `created` (there was none), `kept`
/// (healthy) or `recreated` (the previous one, `previous_id`, was archived and replaced: `reason`).
public struct EnsureAction: Decodable, Hashable, Sendable {
    public var id: String
    public var action: String
    public var reason: String?
    public var previousId: String?
    enum CodingKeys: String, CodingKey { case id, action, reason, previousId = "previous_id" }
}

/// `POST /note/processor/ensure`: the processor (note-drain) and reflect automations, made healthy.
public struct ProcessorEnsure: Decodable, Hashable, Sendable {
    public var processorId: String
    public var reflectId: String
    public var created: [String]
    public var processor: EnsureAction
    public var reflect: EnsureAction
    enum CodingKeys: String, CodingKey { case processorId = "processor_id", reflectId = "reflect_id", created, processor, reflect }
}

/// An entity's canonical name for reading: the API's `name` (what clients show) with its `_`
/// separators as spaces ("laurent_philippe_albou" → "laurent philippe albou").
public func readableName(_ name: String) -> String { name.replacingOccurrences(of: "_", with: " ") }

/// Answers cite their notes inline as `[nt_…]` (cognition.md); a watch shows the sources as rows
/// under the answer, so the markers are taken out of the text.
public enum Citations {
    nonisolated(unsafe) private static let marker = try! NSRegularExpression(pattern: #"\s*\[nt_[A-Za-z0-9_]+\]"#)
    public static func strip(_ text: String) -> String {
        let r = NSRange(text.startIndex..., in: text)
        return marker.stringByReplacingMatches(in: text, range: r, withTemplate: "").trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
