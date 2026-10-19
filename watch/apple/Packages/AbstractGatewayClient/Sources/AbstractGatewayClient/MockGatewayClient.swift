import Foundation

/// A notebook in memory for previews, screenshots and UI tests (`--mock`). It answers the same
/// calls as the live client with the documented shapes; nothing leaves the watch.
public actor MockGatewayClient: GatewayClient {
    public nonisolated let isMock = true

    /// How long a started capture or question takes to answer (`.seconds(3600)`: the spinner stays).
    private let answerDelay: Duration
    /// How every call fails, for the error screens: no radio, signed out, or AbstractNote missing.
    public enum Failure: Sendable { case offline, unauthorized, notSetUp }
    private let failure: Failure?
    /// Today has nothing new.
    private let quiet: Bool

    private var started: [String: (kind: String, at: ContinuousClock.Instant, text: String)] = [:]
    private var counter = 0
    private var ackedInsights: Set<String> = []
    private var closedLoops: Set<String> = []
    public private(set) var captures: [Capture] = []

    /// False: the server has no speech to text (its transcription answers a server error).
    private let canTranscribe: Bool

    public init(answerDelay: Duration = .milliseconds(800), failure: Failure? = nil, quiet: Bool = false, canTranscribe: Bool = true) {
        self.answerDelay = answerDelay; self.failure = failure; self.quiet = quiet; self.canTranscribe = canTranscribe
    }

    private func check() throws {
        switch failure {
        case .offline?: throw URLError(.notConnectedToInternet)
        case .unauthorized?: throw GatewayError.unauthorized
        case .notSetUp?: throw GatewayError.http(status: 404, body: #"{"detail":"Not Found"}"#)
        case nil: return
        }
    }

    public func health() async throws -> Bool { try check(); return true }
    public func me() async throws -> Principal { try check(); return Principal(userId: "laurent", tenantId: nil, roles: ["user"]) }

    public func startCapture(_ capture: Capture) async throws -> String {
        try check()
        captures.append(capture)
        counter += 1
        let id = "mock-capture-\(counter)"
        started[id] = ("capture", .now, capture.text)
        return id
    }

    public func startRecall(question: String, effort: RecallEffort, sessionId: String) async throws -> String {
        try check()
        counter += 1
        let id = "mock-recall-\(effort.rawValue)-\(counter)"
        started[id] = ("recall", .now, question)
        return id
    }

    public func run(id: String) async throws -> RunSummary {
        try check()
        guard let s = started[id] else { throw GatewayError.http(status: 404, body: "{\"detail\":\"Run '\(id)' not found\"}") }
        guard ContinuousClock.now - s.at >= answerDelay else { return RunSummary(runId: id, status: "running") }
        let output: JSONValue
        if s.kind == "capture" {
            output = .object(["response": .string(Self.filingAnswer), "note_id": .string("n-\(counter)"), "status": .string("new")])
        } else {
            output = .object([
                "response": .string(Self.recallAnswer),
                "evidence": .array(Self.evidence.map { e in
                    .object(["note_id": .string(e.noteId), "title": .string(e.title ?? ""), "occurred_at": .string(e.occurredAt ?? "")])
                }),
            ])
        }
        return RunSummary(runId: id, status: "completed", output: output)
    }

    public func overview() async throws -> NoteOverview {
        try check()
        return NoteOverview(resurfacing: quiet ? [] : [NoteRef(noteId: "n-2025-10-19", title: "First sketch of the reading list", kind: "idea", occurredAt: "2025-10-19T08:30:00Z")])
    }

    public func changes(since cursor: String?) async throws -> NoteChanges {
        try check()
        if quiet { return NoteChanges(cursor: "c-2") }
        return NoteChanges(cursor: "c-2",
                           notes: [NoteRef(noteId: "n-41", title: "Pareto by Friday", kind: "task")],
                           entities: [EntityRef(entityId: "ex:person-dana", name: "Dana", kind: "person")],
                           supersededClaims: [ClaimChange(claimId: "c-9", text: "The LUT budget is 40k")],
                           closedLoops: [OpenLoop(taskId: "t-3", text: "Send the slides to Marc", status: "done")])
    }

    public func cues(limit: Int) async throws -> [Cue] {
        try check()
        return quiet ? [] : [Cue(cueId: "cue-1", text: "You have not mentioned Marc in five weeks. Anything new?", kind: "quiet_person",
                                     subject: NoteSubject(kind: "entity", id: "ex:person-marc", title: "Marc"),
                                     evidenceNoteIds: ["n-30", "n-31"], entityIds: ["ex:person-marc"])]
    }

    public func openLoops(limit: Int) async throws -> [OpenLoop] {
        try check()
        let all = quiet ? [] : [OpenLoop(taskId: "t-7", noteId: "n-40", text: "Send Dana the Pareto", due: "2026-10-23"),
                                OpenLoop(taskId: "t-8", noteId: "n-33", text: "Book the train to Lyon")]
        return all.filter { !closedLoops.contains($0.taskId) }.prefix(limit).map { $0 }
    }

    public func insights(limit: Int) async throws -> [Insight] {
        try check()
        let all = quiet ? [] : [Insight(noteId: "i-1", title: "The LUT budget changed twice this month",
                                         body: "In September you planned 40k; on 12 October Dana said 55k; on 17 October you noted 50k.",
                                         insightKind: "evolution", occurredAt: "2026-10-18T06:00:00Z")]
        return all.filter { !ackedInsights.contains($0.noteId) }.prefix(limit).map { $0 }
    }

    public func acknowledge(insightId: String) async throws { try check(); ackedInsights.insert(insightId) }
    public func close(taskId: String) async throws { try check(); closedLoops.insert(taskId) }

    public func note(id: String) async throws -> NoteDetail {
        try check()
        if let m = Self.marcNotes[id] {
            return NoteDetail(noteId: id, title: m.title, text: m.text, kind: "event", occurredAt: m.at)
        }
        let e = Self.evidence.first { $0.noteId == id }
        return NoteDetail(noteId: id, title: e?.title ?? "A note", text: "Met Dana about the LUT budget; she wants the Pareto by Friday.",
                          kind: "decision", occurredAt: e?.occurredAt ?? "2026-10-12T09:12:00Z")
    }

    public func signOut() async {}

    public func noteStatus() async throws { try check() }

    public private(set) var ensures = 0

    public func ensureProcessor() async throws -> ProcessorEnsure {
        try check()
        ensures += 1
        let body = #"{"processor_id":"p-1","reflect_id":"r-1","created":[],"processor":{"id":"p-1","action":"kept","reason":null},"reflect":{"id":"r-1","action":"kept","reason":null}}"#
        return try JSONDecoder().decode(ProcessorEnsure.self, from: Data(body.utf8))
    }

    public func upload(wav: Data, sessionId: String) async throws -> UploadedAttachment {
        try check()
        counter += 1
        let id = "mock-audio-\(counter)"
        let ref: JSONValue = .object(["$artifact": .string(id), "artifact_id": .string(id), "content_type": .string("audio/wav"),
                                      "filename": .string("note.wav"), "run_id": .string("session_memory_\(sessionId)")])
        return UploadedAttachment(reference: ref, artifactId: id, ownerRunId: "session_memory_\(sessionId)")
    }

    public func transcribe(_ audio: UploadedAttachment) async throws -> String {
        try check()
        try await Task.sleep(for: answerDelay)
        guard canTranscribe else { throw GatewayError.transcriptionUnavailable(body: #"{"detail":"No STT engine is available."}"#) }
        return Self.transcript
    }

    public static let transcript = "Call Marc about restarting the reading group in November."

    public static let filingAnswer = "Saved as idea \u{2018}LUT budget\u{2019}. Related: Dana, LUT."
    public static let recallAnswer = "You settled on 50k on 17 October, after Dana asked for 55k; in September the plan was 40k."
    static let marcNotes: [String: (title: String, text: String, at: String)] = [
        "n-30": ("Slides for Marc", "Send the slides to Marc before the review.", "2026-09-08T10:00:00Z"),
        "n-31": ("Lunch with Marc", "Marc wants to restart the reading group in October.", "2026-09-14T12:30:00Z"),
    ]

    public static let evidence = [
        Evidence(noteId: "n-12", title: "LUT budget with Dana", occurredAt: "2026-10-12T09:12:00Z"),
        Evidence(noteId: "n-17", title: "Budget settled at 50k", occurredAt: "2026-10-17T16:40:00Z"),
    ]
}
