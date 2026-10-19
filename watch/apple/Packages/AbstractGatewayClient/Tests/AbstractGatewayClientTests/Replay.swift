import Foundation
import Testing
@testable import AbstractGatewayClient

/// One recorded exchange: `{"request": {"method", "path"}, "status", "body"}`.
struct Fixture {
    let method: String
    let path: String
    let status: Int
    let body: Data

    init(json data: Data, name: String) throws {
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let req = obj["request"] as? [String: Any], let method = req["method"] as? String, let path = req["path"] as? String,
              let status = obj["status"] as? Int, let body = obj["body"] else {
            throw FixtureError.malformed(name)
        }
        self.method = method; self.path = path; self.status = status
        self.body = try JSONSerialization.data(withJSONObject: body, options: [.fragmentsAllowed, .sortedKeys])
    }

    /// A fixture shipped with the tests (Fixtures/<name>.json).
    static func named(_ name: String) throws -> Fixture {
        guard let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures") else {
            throw FixtureError.missing(name)
        }
        return try Fixture(json: try Data(contentsOf: url), name: name)
    }
}

enum FixtureError: Error { case missing(String), malformed(String) }

/// Every suite that uses `ReplayStub` is nested here: the stub's queue is shared, so they run one
/// test at a time, never two suites side by side.
@Suite(.serialized) enum ReplayGroup {}

/// Every request is answered by the next queued fixture; the requests are kept for inspection.
final class ReplayStub: URLProtocol, @unchecked Sendable {
    struct Seen { let method: String; let path: String; let query: [String: String]; let body: [String: Any] }
    nonisolated(unsafe) static var queue: [Fixture] = []
    nonisolated(unsafe) static var seen: [Seen] = []

    static func reset(_ fixtures: [Fixture]) { queue = fixtures; seen = [] }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        var body: [String: Any] = [:]
        if let data = request.httpBody ?? Self.read(request.httpBodyStream) {
            body = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
        }
        let comps = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
        let query = Dictionary((comps?.queryItems ?? []).map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { a, _ in a })
        Self.seen.append(Seen(method: request.httpMethod ?? "", path: request.url!.path(), query: query, body: body))
        guard !Self.queue.isEmpty else {
            client?.urlProtocol(self, didFailWithError: URLError(.resourceUnavailable)); return
        }
        let f = Self.queue.removeFirst()
        let resp = HTTPURLResponse(url: request.url!, statusCode: f.status, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: f.body)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}

    private static func read(_ stream: InputStream?) -> Data? {
        guard let stream else { return nil }
        stream.open(); defer { stream.close() }
        var data = Data(); var buf = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable { let n = stream.read(&buf, maxLength: 4096); if n <= 0 { break }; data.append(buf, count: n) }
        return data
    }

    static func client(_ credential: GatewayCredential = .token("agw_t")) -> LiveGatewayClient {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [ReplayStub.self]
        return LiveGatewayClient(baseURL: URL(string: "https://gateway.example")!, credential: credential, session: URLSession(configuration: cfg))
    }
}
