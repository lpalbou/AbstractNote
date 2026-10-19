import Foundation

// The request path of the AbstractAssistant watch client's GatewayClient.swift (SNAPSHOT.json),
// shared by every watch app: reads retried after 1 s then 3 s, writes sent once, a 401 locks the
// transport out until the next sign-in; plus the attachment upload and the gateway's speech to text.

public enum GatewayError: Error, Equatable, Sendable, LocalizedError {
    /// 401: the credential is wrong or ended. The gateway locks out by client address after 5
    /// failures, so the transport stops sending until the person signs in again.
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
        case .unauthorized: "Sign in to your notebook."
        case .conflict: "Already done on another device."
        case .tooLarge: "This is too long to send from the watch."
        case .http(let s, let body): GatewayErrorBody.parse(body)?.message.flatMap(Problem.sentence) ?? (s >= 500 ? "The server is having trouble. Try again." : "That didn't work. Try again.")
        case .invalidResponse: "Can't reach the server."
        case .unexpectedAnswer: "This answer could not be read. Update the app."
        }
    }
}

/// An uploaded file as the gateway answers it: the `attachment` reference to pass on unchanged
/// (`{"$artifact": id, ...}`), its id, and the run that owns it (for speech to text).
public struct UploadedAttachment: Sendable, Hashable {
    public var reference: JSONValue
    public var artifactId: String
    public var ownerRunId: String
    public init(reference: JSONValue, artifactId: String, ownerRunId: String) {
        self.reference = reference; self.artifactId = artifactId; self.ownerRunId = ownerRunId
    }
}

/// HTTP to one gateway with one credential.
public actor GatewayTransport {
    public let baseURL: URL
    private let credential: GatewayCredential
    private let session: URLSession
    private var lockedOut = false

    public init(baseURL: URL, credential: GatewayCredential, session: URLSession = .shared) {
        self.baseURL = baseURL; self.credential = credential; self.session = session
    }

    /// How many times a read is tried before its failure is reported.
    public static let readTries = 3

    /// A read (GET) that times out, loses its connection or meets a server error is tried again,
    /// after 1 s then 3 s: a screen keeps its spinner through the tries and says a read failed
    /// only once every try has (ADR-0039 in abstractframework docs/adr). A write (POST, PUT, …) is sent once.
    public func request(_ method: String, _ path: String, query: [URLQueryItem] = [], body: (any Encodable & Sendable)? = nil,
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

    private func send(_ method: String, _ path: String, query: [URLQueryItem], body: (any Encodable)?, timeout: TimeInterval) async throws -> Data {
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
        return try check(try await session.data(for: req))
    }

    private func check(_ answer: (Data, URLResponse)) throws -> Data {
        let (data, resp) = answer
        guard let http = resp as? HTTPURLResponse else { throw GatewayError.invalidResponse }
        switch http.statusCode {
        case 200..<300: return data
        case 401: lockedOut = true; throw GatewayError.unauthorized
        case 409: throw GatewayError.conflict(body: String(decoding: data, as: UTF8.self))
        case 413: throw GatewayError.tooLarge
        default: throw GatewayError.http(status: http.statusCode, body: String(decoding: data, as: UTF8.self))
        }
    }

    public func get<T: Decodable>(_ path: String, query: [URLQueryItem] = [], as: T.Type, timeout: TimeInterval = 20) async throws -> T {
        let data = try await request("GET", path, query: query, timeout: timeout)
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw GatewayError.unexpectedAnswer("\(path): \(error)") }
    }

    /// An id placed in a path: one segment, never a way out of it.
    public static func segment(_ id: String) throws -> String {
        guard !id.isEmpty, id != ".", id != "..", !id.contains("/"), !id.contains("?"), !id.contains("#") else {
            throw GatewayError.invalidResponse
        }
        return id
    }

    /// `GET /api/gateway/me`
    public func me() async throws -> Principal { try await get("api/gateway/me", as: Principal.self) }

    /// `POST /api/gateway/attachments/upload` (multipart: `session_id`, `file`), sent once.
    public func upload(_ data: Data, filename: String, contentType: String, sessionId: String) async throws -> UploadedAttachment {
        if lockedOut { throw GatewayError.unauthorized }
        let boundary = "watch-" + UUID().uuidString
        var body = Data()
        func field(_ name: String, _ value: String) {
            body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".utf8))
        }
        field("session_id", sessionId); field("content_type", contentType); field("filename", filename)
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\nContent-Type: \(contentType)\r\n\r\n".utf8))
        body.append(data)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        var req = URLRequest(url: baseURL.appending(path: "api/gateway/attachments/upload"))
        req.httpMethod = "POST"; req.timeoutInterval = 60; req.httpBody = body
        credential.apply(to: &req, write: true)
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let answer = try check(try await session.data(for: req))
        return try Self.uploaded(answer, sessionId: sessionId)
    }

    /// Reads the upload answer (recorded: attachments_upload_200): `attachment` (or `artifact`) is
    /// the reference; its owner is `run_id` (`session_memory_<session>`).
    public static func uploaded(_ data: Data, sessionId: String) throws -> UploadedAttachment {
        guard let v = try? JSONDecoder().decode(JSONValue.self, from: data),
              let ref = v["attachment"] ?? v["artifact"], let id = ref["$artifact"]?.stringValue, !id.isEmpty else {
            throw GatewayError.unexpectedAnswer("attachments/upload: no $artifact")
        }
        let owner = v["run_id"]?.stringValue ?? ref["run_id"]?.stringValue ?? "session_memory_\(sessionId)"
        return UploadedAttachment(reference: ref, artifactId: id, ownerRunId: owner)
    }

    private struct TranscribeRequest: Encodable, Sendable {
        let audio_artifact: [String: String]
    }
    private struct TranscribeResponse: Decodable { let text: String }

    /// What was said in an uploaded recording: `POST /api/gateway/runs/{owner}/audio/transcribe`
    /// `{audio_artifact: {"$artifact": id}}` with the gateway's speech to text.
    public func transcribe(_ audio: UploadedAttachment) async throws -> String {
        let data = try await request("POST", "api/gateway/runs/\(try Self.segment(audio.ownerRunId))/audio/transcribe",
                                     body: TranscribeRequest(audio_artifact: ["$artifact": audio.artifactId]), timeout: 120)
        do { return try JSONDecoder().decode(TranscribeResponse.self, from: data).text.trimmingCharacters(in: .whitespacesAndNewlines) }
        catch { throw GatewayError.unexpectedAnswer("audio/transcribe: \(error)") }
    }

    private struct Empty: Encodable, Sendable {}

    /// `POST /api/gateway/session/logout` for an emailed-code session (once); nothing for a token.
    public func signOut() async {
        guard case .session = credential, !lockedOut else { return }
        _ = try? await send("POST", "api/gateway/session/logout", query: [], body: Empty(), timeout: 10)
        lockedOut = true
    }
}
