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
    public init(text: String, occurredAt: Date, location: CaptureLocation?, idempotencyKey: String, sessionId: String, about: NoteSubject? = nil) {
        self.text = text; self.occurredAt = occurredAt; self.location = location
        self.idempotencyKey = idempotencyKey; self.sessionId = sessionId; self.about = about
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
    struct About: Encodable { var about: NoteSubject }
    struct Input: Encodable {
        var text: String
        var occurred_at: String
        var location: CaptureLocation?
        var source = Source()
        var idempotency_key: String
        var process_inline = false
        /// The gateway does not hand the request's `session_id` to the flow: it goes here too.
        var session_id: String
        var context: About?
    }
    var bundle_id = NoteFlows.bundleId
    var flow_id = NoteFlows.capture
    var session_id: String
    var input_data: Input

    init(_ c: Capture) {
        session_id = c.sessionId
        input_data = Input(text: c.text, occurred_at: ISO8601.string(c.occurredAt), location: c.location,
                           idempotency_key: c.idempotencyKey, session_id: c.sessionId, context: c.about.map { About(about: $0) })
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

    /// "Runtime error: Local file paths are not accepted here." → "Local file paths are not accepted here."
    public static func cleanError(_ text: String?) -> String? {
        guard var t = text?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        let prefix = "Runtime error: "
        if t.hasPrefix(prefix) { t = String(t.dropFirst(prefix.count)).trimmingCharacters(in: .whitespaces) }
        return t.isEmpty ? nil : t
    }
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

/// `GET /note/notes/{id}`: the watch shows a note's text, when it was written, and for an insight
/// the notes behind it. Every other key the gateway adds is ignored.
public struct NoteDetail: Decodable, Hashable, Sendable, Identifiable {
    public var noteId: String
    public var title: String?
    public var text: String
    public var kind: String?
    public var occurredAt: String?
    public var insight: InsightFacts?
    public var id: String { noteId }
    enum CodingKeys: String, CodingKey { case noteId = "note_id", title, text, kind, occurredAt = "occurred_at", insight }
    public init(noteId: String, title: String?, text: String, kind: String? = nil, occurredAt: String? = nil, insight: InsightFacts? = nil) {
        self.noteId = noteId; self.title = title; self.text = text; self.kind = kind; self.occurredAt = occurredAt; self.insight = insight
    }
}

extension Optional where Wrapped == String {
    /// A title to show: nil when absent or blank (the gateway sends "" for an untitled note).
    public var shown: String? {
        guard let s = self?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
        return s
    }
}
