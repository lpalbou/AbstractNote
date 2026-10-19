@_exported import GatewayCore
import Foundation

// The AbstractNote calls (docs/api.md) over AbstractWatchKit's shared transport. Started as a
// snapshot of the AbstractAssistant watch client (SNAPSHOT.json in Packages/AbstractWatchKit).

/// The calls the watch makes (abstractnote docs/api.md, docs/clients.md).
public protocol GatewayClient: Sendable {
    var isMock: Bool { get }
    /// `GET /api/health`
    func health() async throws -> Bool
    /// `GET /api/gateway/me`
    func me() async throws -> Principal
    /// `POST /api/gateway/runs/start` with `note-capture`; returns the run id.
    func startCapture(_ capture: Capture) async throws -> String
    /// `POST /api/gateway/runs/start` with `note-recall` `{question, effort}`; returns the run id.
    func startRecall(question: String, effort: RecallEffort, sessionId: String) async throws -> String
    /// `GET /api/gateway/runs/{id}`
    func run(id: String) async throws -> RunSummary
    /// `GET /api/gateway/note/overview`
    func overview() async throws -> NoteOverview
    /// `GET /api/gateway/note/changes?since=`: nil asks for the server's default window.
    func changes(since cursor: String?) async throws -> NoteChanges
    /// `GET /api/gateway/note/cues?limit=`
    func cues(limit: Int) async throws -> [Cue]
    /// `GET /api/gateway/note/tasks?status=open&limit=`
    func openLoops(limit: Int) async throws -> [OpenLoop]
    /// `GET /api/gateway/note/insights?acked=false&limit=`
    func insights(limit: Int) async throws -> [Insight]
    /// `POST /api/gateway/note/insights/{id}/ack`
    func acknowledge(insightId: String) async throws
    /// `POST /api/gateway/note/tasks/{id}/close`
    func close(taskId: String) async throws
    /// `GET /api/gateway/note/notes/{id}`
    func note(id: String) async throws -> NoteDetail
    /// `GET /api/gateway/note/status`: answers 404 when AbstractNote is not set up on the server.
    func noteStatus() async throws
    /// `POST /api/gateway/attachments/upload` of a recording (WAV), in the watch's session.
    func upload(wav: Data, sessionId: String) async throws -> UploadedAttachment
    /// `POST /api/gateway/runs/{owner}/audio/transcribe`: what was said in an uploaded recording.
    func transcribe(_ audio: UploadedAttachment) async throws -> String
    /// `POST /api/gateway/session/logout` for an emailed-code session; nothing for a token.
    func signOut() async
}

extension GatewayClient {
    /// Where a capture stands: `output.response` (the filing sentence), `output.note_id`, `output.status`.
    public func captureProgress(runId: String) async throws -> RunProgress<CaptureAnswer> {
        try Self.progress(try await run(id: runId), as: CaptureAnswer.self)
    }

    /// Where a question stands: `output.response` and `output.evidence`.
    public func recallProgress(runId: String) async throws -> RunProgress<RecallAnswer> {
        try Self.progress(try await run(id: runId), as: RecallAnswer.self)
    }

    static func progress<A: Decodable & Sendable & Hashable>(_ run: RunSummary, as: A.Type) throws -> RunProgress<A> {
        switch run.status {
        case "completed":
            guard let output = run.output else { throw GatewayError.unexpectedAnswer("completed run \(run.runId) has no output") }
            // A flow that refused its input completes with `{success: false, error}` (recorded:
            // run_capture_path_refused): a failure with its sentence, not an unreadable answer.
            if case .bool(false)? = output["success"] {
                return .failed(Problem.sentence(output["error"]?.stringValue ?? run.error))
            }
            do { return .done(try output.decode(A.self)) }
            catch { throw GatewayError.unexpectedAnswer("run \(run.runId) output: \(error)") }
        case "failed", "cancelled": return .failed(Problem.sentence(run.error))
        default: return run.waitsOnPerson ? .needsPerson : .working
        }
    }

    /// Polls a run every `every` until it is done, fails, waits on a person, or `timeout` passes
    /// (then `.working`: the answer is late, not lost). A failed read is retried by the client.
    public func follow<A: Sendable & Hashable>(_ read: @Sendable () async throws -> RunProgress<A>,
                                               every: Duration = .seconds(1), timeout: Duration = .seconds(90)) async throws -> RunProgress<A> {
        let clock = ContinuousClock()
        let deadline = clock.now + timeout
        while true {
            let p = try await read()
            if case .working = p, clock.now < deadline, !Task.isCancelled {
                try await Task.sleep(for: every)
                continue
            }
            return p
        }
    }
}

/// Live client: the note calls over the shared transport (AbstractWatchKit's GatewayCore), with a
/// Bearer token or an emailed-code session.
public actor LiveGatewayClient: GatewayClient {
    public nonisolated let isMock = false
    private let transport: GatewayTransport

    public init(baseURL: URL, credential: GatewayCredential, session: URLSession = .shared) {
        transport = GatewayTransport(baseURL: baseURL, credential: credential, session: session)
    }

    public init(baseURL: URL, token: String, session: URLSession = .shared) {
        self.init(baseURL: baseURL, credential: .token(token), session: session)
    }

    /// How many times a read is tried before its failure is reported (the transport's rule).
    public static let readTries = GatewayTransport.readTries

    static func segment(_ id: String) throws -> String { try GatewayTransport.segment(id) }

    public func health() async throws -> Bool {
        _ = try await transport.request("GET", "api/health"); return true
    }

    public func me() async throws -> Principal { try await transport.me() }

    public func startCapture(_ capture: Capture) async throws -> String {
        try decodeRunId(try await transport.request("POST", "api/gateway/runs/start", body: CaptureStart(capture), timeout: 30))
    }

    public func startRecall(question: String, effort: RecallEffort, sessionId: String) async throws -> String {
        let body = RecallStart(session_id: sessionId, input_data: .init(question: question, effort: effort.rawValue, session_id: sessionId))
        return try decodeRunId(try await transport.request("POST", "api/gateway/runs/start", body: body, timeout: 30))
    }

    private func decodeRunId(_ data: Data) throws -> String {
        do { return try JSONDecoder().decode(StartRunResponse.self, from: data).runId }
        catch { throw GatewayError.unexpectedAnswer("runs/start: \(error)") }
    }

    public func run(id: String) async throws -> RunSummary {
        try await transport.get("api/gateway/runs/\(try Self.segment(id))", as: RunSummary.self)
    }

    public func overview() async throws -> NoteOverview {
        try await transport.get("api/gateway/note/overview", query: [.init(name: "days", value: "1")], as: NoteOverview.self, timeout: 30)
    }

    public func changes(since cursor: String?) async throws -> NoteChanges {
        var q = [URLQueryItem(name: "limit", value: "20")]
        if let cursor { q.append(.init(name: "since", value: cursor)) }
        return try await transport.get("api/gateway/note/changes", query: q, as: NoteChanges.self, timeout: 30)
    }

    public func cues(limit: Int) async throws -> [Cue] {
        try await transport.get("api/gateway/note/cues", query: [.init(name: "limit", value: String(limit))], as: ListPage<Cue>.self).items
    }

    public func openLoops(limit: Int) async throws -> [OpenLoop] {
        try await transport.get("api/gateway/note/tasks", query: [.init(name: "status", value: "open"), .init(name: "limit", value: String(limit))],
                                as: ListPage<OpenLoop>.self).items
    }

    public func insights(limit: Int) async throws -> [Insight] {
        try await transport.get("api/gateway/note/insights", query: [.init(name: "acked", value: "false"), .init(name: "limit", value: String(limit))],
                                as: ListPage<Insight>.self).items
    }

    private struct Empty: Encodable, Sendable {}

    public func acknowledge(insightId: String) async throws {
        _ = try await transport.request("POST", "api/gateway/note/insights/\(try Self.segment(insightId))/ack", body: Empty())
    }

    public func close(taskId: String) async throws {
        _ = try await transport.request("POST", "api/gateway/note/tasks/\(try Self.segment(taskId))/close", body: Empty())
    }

    public func note(id: String) async throws -> NoteDetail {
        try await transport.get("api/gateway/note/notes/\(try Self.segment(id))", as: NoteDetail.self)
    }

    public func noteStatus() async throws {
        _ = try await transport.request("GET", "api/gateway/note/status")
    }

    public func upload(wav: Data, sessionId: String) async throws -> UploadedAttachment {
        try await transport.upload(wav, filename: "note.wav", contentType: "audio/wav", sessionId: sessionId)
    }

    public func transcribe(_ audio: UploadedAttachment) async throws -> String { try await transport.transcribe(audio) }

    public func signOut() async { await transport.signOut() }
}
