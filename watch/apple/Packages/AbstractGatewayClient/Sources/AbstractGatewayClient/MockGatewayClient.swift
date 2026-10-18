import Foundation

/// A notebook in memory for previews, screenshots and UI tests (`--mock`). It answers the same
/// calls as the live client with the documented shapes; nothing leaves the watch.
public actor MockGatewayClient: GatewayClient {
    public nonisolated let isMock = true

    /// How long a started capture or question takes to answer (`.seconds(3600)`: the spinner stays).
    private let answerDelay: Duration
    /// Every read and write fails as if the radio were off.
    private let offline: Bool
    /// Today has nothing new.
    private let quiet: Bool

    private var started: [String: (kind: String, at: ContinuousClock.Instant, text: String)] = [:]
    private var counter = 0
    private var ackedInsights: Set<String> = []
    private var closedLoops: Set<String> = []
    public private(set) var captures: [Capture] = []

    public init(answerDelay: Duration = .milliseconds(800), offline: Bool = false, quiet: Bool = false) {
        self.answerDelay = answerDelay; self.offline = offline; self.quiet = quiet
    }

    private func check() throws { if offline { throw URLError(.notConnectedToInternet) } }

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
        return quiet ? [] : [Cue(cueId: "cue-1", text: "You have not mentioned Marc in five weeks. Anything new?", kind: "quiet_person")]
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
        let e = Self.evidence.first { $0.noteId == id }
        return NoteDetail(noteId: id, title: e?.title ?? "A note", text: "Met Dana about the LUT budget; she wants the Pareto by Friday.",
                          kind: "decision", occurredAt: e?.occurredAt ?? "2026-10-12T09:12:00Z")
    }

    public func signOut() async {}

    public static let filingAnswer = "Saved as idea \u{2018}LUT budget\u{2019}. Related: Dana, LUT."
    public static let recallAnswer = "You settled on 50k on 17 October, after Dana asked for 55k; in September the plan was 40k."
    public static let evidence = [
        Evidence(noteId: "n-12", title: "LUT budget with Dana", occurredAt: "2026-10-12T09:12:00Z"),
        Evidence(noteId: "n-17", title: "Budget settled at 50k", occurredAt: "2026-10-17T16:40:00Z"),
    ]
}
