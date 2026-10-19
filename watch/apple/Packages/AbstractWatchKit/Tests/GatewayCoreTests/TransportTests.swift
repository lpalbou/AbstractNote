import Foundation
import Testing
@testable import GatewayCore

/// Answers every request with a queued (status, body); keeps what was sent.
final class Stub: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var answers: [(Int, String)] = []
    nonisolated(unsafe) static var sent: [(method: String, path: String, contentType: String?, body: Data)] = []
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        var body = request.httpBody ?? Data()
        if body.isEmpty, let s = request.httpBodyStream {
            s.open(); var buf = [UInt8](repeating: 0, count: 4096)
            while s.hasBytesAvailable { let n = s.read(&buf, maxLength: 4096); if n <= 0 { break }; body.append(buf, count: n) }
            s.close()
        }
        Self.sent.append((request.httpMethod ?? "", request.url!.path(), request.value(forHTTPHeaderField: "Content-Type"), body))
        let (status, text) = Self.answers.isEmpty ? (200, "{}") : Self.answers.removeFirst()
        let resp = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(text.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}

    static func transport(_ credential: GatewayCredential = .token("t")) -> GatewayTransport {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [Stub.self]
        return GatewayTransport(baseURL: URL(string: "https://gateway.example")!, credential: credential, session: URLSession(configuration: cfg))
    }
}

/// The upload answer, trimmed from the recording `attachments_upload_200` (hermetic gateway, 2026-10-19).
let recordedUpload = #"""
{"ok": true, "run_id": "session_memory_s-smoke-edfde0d3",
 "artifact": {"$artifact": "f82202d00e362f35827cd0f731987a5b", "artifact_id": "f82202d00e362f35827cd0f731987a5b", "content_type": "text/plain", "run_id": "session_memory_s-smoke-edfde0d3", "filename": "budget.txt"},
 "attachment": {"$artifact": "f82202d00e362f35827cd0f731987a5b", "artifact_id": "f82202d00e362f35827cd0f731987a5b", "content_type": "text/plain", "modality": "text", "run_id": "session_memory_s-smoke-edfde0d3", "filename": "budget.txt", "target": "client"}}
"""#

@Suite(.serialized) struct TransportTests {
    @Test func aRecordingIsUploadedThenTranscribedByItsOwnerRun() async throws {
        Stub.answers = [(200, recordedUpload), (200, #"{"text": "  Call Marc.  ", "language": "en"}"#)]; Stub.sent = []
        let t = Stub.transport()
        let up = try await t.upload(Data("RIFFwav".utf8), filename: "note.wav", contentType: "audio/wav", sessionId: "note-watch-1")
        #expect(up.artifactId == "f82202d00e362f35827cd0f731987a5b" && up.ownerRunId == "session_memory_s-smoke-edfde0d3")
        #expect(up.reference["$artifact"]?.stringValue == up.artifactId && up.reference["modality"]?.stringValue == "text")
        #expect(try await t.transcribe(up) == "Call Marc.")
        let multipart = String(decoding: Stub.sent[0].body, as: UTF8.self)
        #expect(Stub.sent[0].path == "/api/gateway/attachments/upload" && Stub.sent[0].contentType?.hasPrefix("multipart/form-data") == true)
        #expect(multipart.contains("name=\"session_id\"\r\n\r\nnote-watch-1") && multipart.contains("filename=\"note.wav\"") && multipart.contains("RIFFwav"))
        #expect(Stub.sent[1].method == "POST" && Stub.sent[1].path == "/api/gateway/runs/session_memory_s-smoke-edfde0d3/audio/transcribe")
        let body = try JSONSerialization.jsonObject(with: Stub.sent[1].body) as? [String: Any]
        #expect((body?["audio_artifact"] as? [String: String])?["$artifact"] == "f82202d00e362f35827cd0f731987a5b")
    }

    @Test func anUploadWithoutAnArtifactFailsLoudly() {
        #expect(throws: GatewayError.self) { try GatewayTransport.uploaded(Data(#"{"ok":true}"#.utf8), sessionId: "s") }
        // No run id anywhere: the session's own memory run owns it.
        let up = try? GatewayTransport.uploaded(Data(#"{"attachment":{"$artifact":"a1"}}"#.utf8), sessionId: "s1")
        #expect(up?.ownerRunId == "session_memory_s1")
    }

    @Test func anUploadThatIsTooLargeSaysSo() async {
        Stub.answers = [(413, #"{"detail":"Attachment too large (1048577 bytes > 1048576 bytes)"}"#)]; Stub.sent = []
        await #expect(throws: GatewayError.tooLarge) {
            try await Stub.transport().upload(Data(count: 10), filename: "note.wav", contentType: "audio/wav", sessionId: "s")
        }
    }
}

/// The operator's rule: a specific sentence for each failure.
@Test func failuresBecomeSpecificSentences() {
    #expect(Problem.of(GatewayError.unauthorized, host: "h") == .signIn)
    #expect(Problem.signIn.sentence == "Sign in to your notebook." && Problem.signIn.action == .signIn)
    #expect(Problem.of(URLError(.timedOut), host: "192.168.1.175").sentence == "Can't reach 192.168.1.175.")
    #expect(Problem.of(GatewayError.invalidResponse, host: "h").action == .retry)
    #expect(Problem.of(GatewayError.http(status: 404, body: #"{"detail":"Not Found"}"#), host: "h") == .notFound("Not Found"))
    #expect(Problem.notSetUp(app: "AbstractNote").sentence == "AbstractNote isn't set up on this server yet.")
    #expect(Problem.of(GatewayError.http(status: 400, body: #"{"detail":"Runtime error: Text is empty."}"#), host: "h").sentence == "Text is empty.")
    #expect(Problem.of(GatewayError.http(status: 409, body: ""), host: "h").sentence == "The server refused this. Try again.")
    #expect(Problem.of(GatewayError.conflict(body: #"{"detail":{"reason_code":"automation_busy","message":"An occurrence is already running or waiting to run."}}"#), host: "h").sentence
            == "An occurrence is already running or waiting to run.")
    #expect(Problem.of(GatewayError.http(status: 502, body: "<html>"), host: "h").sentence == "The server is having trouble. Try again.")
    #expect(Problem.sentence("Runtime error: Model is busy.") == "Model is busy." && Problem.sentence(" ") == nil)
}
