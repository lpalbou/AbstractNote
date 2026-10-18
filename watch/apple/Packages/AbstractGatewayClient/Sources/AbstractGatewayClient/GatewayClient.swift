import Foundation

// A trimmed snapshot of the AbstractAssistant watch client's GatewayClient.swift (SNAPSHOT.json):
// the request path (reads retried after 1 s then 3 s, writes sent once, a 401 locks the client
// out) is kept as it was; the assistant's calls are replaced by AbstractNote's (docs/api.md).

public enum GatewayError: Error, Equatable, Sendable, LocalizedError {
    /// 401: the credential is wrong or ended. The gateway locks out by client address after 5
    /// failures, so the client stops sending until the person signs in again.
    case unauthorized
    /// 409: the thing was already done (a loop closed, an insight acknowledged) elsewhere.
    case conflict(body: String)
    /// 413: more than the gateway takes in one request.
    case tooLarge
    case http(status: Int, body: String)
    case invalidResponse
    /// A 2xx answer whose JSON is not the documented shape (the server and the app disagree).
    case unexpectedAnswer(String)

    public var errorDescription: String? {
        switch self {
        // Shown to people as is: plain words, no codes (the body stays on the value for logs).
        case .unauthorized: "Please sign in again."
        case .conflict: "Already done on another device."
        case .tooLarge: "This is too long to send from the watch."
        case .http(let s, _): s >= 500 ? "Your notebook is having trouble. Try again." : "That didn't work. Try again."
        case .invalidResponse: "Can't reach your notebook."
        case .unexpectedAnswer: "This answer could not be read. Update the app."
        }
    }
}

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
            do { return .done(try output.decode(A.self)) }
            catch { throw GatewayError.unexpectedAnswer("run \(run.runId) output: \(error)") }
        case "failed", "cancelled": return .failed
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

/// Live client over URLSession: `Authorization: Bearer <token>`, or a gateway session from an
/// emailed sign-in code (`X-AbstractGateway-Session`, plus `X-AbstractGateway-CSRF` on writes).
public actor LiveGatewayClient: GatewayClient {
    public nonisolated let isMock = false
    private let baseURL: URL
    private let credential: GatewayCredential
    private let session: URLSession
    private var lockedOut = false

    public init(baseURL: URL, credential: GatewayCredential, session: URLSession = .shared) {
        self.baseURL = baseURL; self.credential = credential; self.session = session
    }

    public init(baseURL: URL, token: String, session: URLSession = .shared) {
        self.init(baseURL: baseURL, credential: .token(token), session: session)
    }

    /// How many times a read is tried before its failure is reported.
    public static let readTries = 3

    /// A read (GET) that times out, loses its connection or meets a server error is tried again,
    /// after 1 s then 3 s: a screen keeps its spinner through the tries and says a read failed
    /// only once every try has (ADR-0039 in abstractframework docs/adr). A write (POST, PUT, …) is sent once.
    private func request(_ method: String, _ path: String, query: [URLQueryItem] = [], body: (any Encodable)? = nil,
                         timeout: TimeInterval = 20) async throws -> Data {
        var attempt = 1
        while true {
            do { return try await send(method, path, query: query, body: body, timeout: timeout) }
            catch {
                guard method == "GET", attempt < Self.readTries, Self.isTransient(error), !Task.isCancelled else { throw error }
                try await Task.sleep(for: .seconds(attempt == 1 ? 1 : 3))
                attempt += 1
            }
        }
    }

    /// A failure that another try may not meet: no answer in time, a connection lost or refused,
    /// a server error (5xx, 408, 429). A refusal (401, 403, 404, 409, 413, …) or an unreadable answer is final.
    public static func isTransient(_ error: Error) -> Bool {
        switch error {
        case let e as URLError: return ![.cancelled, .badURL, .unsupportedURL, .userAuthenticationRequired, .userCancelledAuthentication].contains(e.code)
        case GatewayError.http(let status, _): return status >= 500 || status == 408 || status == 429
        case GatewayError.invalidResponse: return true
        default: return false
        }
    }

    private func send(_ method: String, _ path: String, query: [URLQueryItem], body: (any Encodable)?,
                      timeout: TimeInterval) async throws -> Data {
        if lockedOut { throw GatewayError.unauthorized }
        var comps = URLComponents(url: baseURL.appending(path: path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty {
            comps.queryItems = query
            // A "+" left as is reads as a space on the server ("C++" would arrive as "C").
            comps.percentEncodedQuery = comps.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        }
        var req = URLRequest(url: comps.url!)
        req.httpMethod = method
        req.timeoutInterval = timeout
        credential.apply(to: &req, write: method != "GET")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode(body)
        }
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw GatewayError.invalidResponse }
        switch http.statusCode {
        case 200..<300: return data
        case 401: lockedOut = true; throw GatewayError.unauthorized
        case 409: throw GatewayError.conflict(body: String(decoding: data, as: UTF8.self))
        case 413: throw GatewayError.tooLarge
        default: throw GatewayError.http(status: http.statusCode, body: String(decoding: data, as: UTF8.self))
        }
    }

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = [], as: T.Type, timeout: TimeInterval = 20) async throws -> T {
        let data = try await request("GET", path, query: query, timeout: timeout)
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw GatewayError.unexpectedAnswer("\(path): \(error)") }
    }

    /// An id placed in a path: one segment, never a way out of it.
    static func segment(_ id: String) throws -> String {
        guard !id.isEmpty, id != ".", id != "..", !id.contains("/"), !id.contains("?"), !id.contains("#") else {
            throw GatewayError.invalidResponse
        }
        return id
    }

    public func health() async throws -> Bool {
        _ = try await request("GET", "api/health"); return true
    }

    public func me() async throws -> Principal { try await get("api/gateway/me", as: Principal.self) }

    public func startCapture(_ capture: Capture) async throws -> String {
        let data = try await request("POST", "api/gateway/runs/start", body: CaptureStart(capture), timeout: 30)
        return try decodeRunId(data)
    }

    public func startRecall(question: String, effort: RecallEffort, sessionId: String) async throws -> String {
        let body = RecallStart(session_id: sessionId, input_data: .init(question: question, effort: effort.rawValue))
        return try decodeRunId(try await request("POST", "api/gateway/runs/start", body: body, timeout: 30))
    }

    private func decodeRunId(_ data: Data) throws -> String {
        do { return try JSONDecoder().decode(StartRunResponse.self, from: data).runId }
        catch { throw GatewayError.unexpectedAnswer("runs/start: \(error)") }
    }

    public func run(id: String) async throws -> RunSummary {
        try await get("api/gateway/runs/\(try Self.segment(id))", as: RunSummary.self)
    }

    public func overview() async throws -> NoteOverview {
        try await get("api/gateway/note/overview", query: [.init(name: "days", value: "1")], as: NoteOverview.self, timeout: 30)
    }

    public func changes(since cursor: String?) async throws -> NoteChanges {
        var q = [URLQueryItem(name: "limit", value: "20")]
        if let cursor { q.append(.init(name: "since", value: cursor)) }
        return try await get("api/gateway/note/changes", query: q, as: NoteChanges.self, timeout: 30)
    }

    public func cues(limit: Int) async throws -> [Cue] {
        try await get("api/gateway/note/cues", query: [.init(name: "limit", value: String(limit))], as: ListPage<Cue>.self).items
    }

    public func openLoops(limit: Int) async throws -> [OpenLoop] {
        try await get("api/gateway/note/tasks", query: [.init(name: "status", value: "open"), .init(name: "limit", value: String(limit))],
                      as: ListPage<OpenLoop>.self).items
    }

    public func insights(limit: Int) async throws -> [Insight] {
        try await get("api/gateway/note/insights", query: [.init(name: "acked", value: "false"), .init(name: "limit", value: String(limit))],
                      as: ListPage<Insight>.self).items
    }

    private struct Empty: Encodable {}

    public func acknowledge(insightId: String) async throws {
        _ = try await request("POST", "api/gateway/note/insights/\(try Self.segment(insightId))/ack", body: Empty())
    }

    public func close(taskId: String) async throws {
        _ = try await request("POST", "api/gateway/note/tasks/\(try Self.segment(taskId))/close", body: Empty())
    }

    public func note(id: String) async throws -> NoteDetail {
        try await get("api/gateway/note/notes/\(try Self.segment(id))", as: NoteDetail.self)
    }

    public func signOut() async {
        guard case .session = credential, !lockedOut else { return }
        _ = try? await send("POST", "api/gateway/session/logout", query: [], body: Empty(), timeout: 10)
        lockedOut = true
    }
}
