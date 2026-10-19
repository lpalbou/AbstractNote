import Foundation

// Wire models for the AbstractGateway HTTP API (abstractgateway docs/api.md, service.py
// `run_summary`). A trimmed snapshot of the AbstractAssistant watch client's Models.swift
// (SNAPSHOT.json): JSONValue, Principal and StartRunResponse are kept as they were; RunSummary
// keeps only what every watch app reads. Field names are snake_case on the wire; every model maps
// them explicitly so a renamed field fails decoding loudly.

/// Any JSON value. Run `output` is free-form.
public enum JSONValue: Codable, Hashable, Sendable {
    case string(String), number(Double), bool(Bool), object([String: JSONValue]), array([JSONValue]), null

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .object(let o): try c.encode(o)
        case .array(let a): try c.encode(a)
        case .null: try c.encodeNil()
        }
    }

    public var stringValue: String? { if case .string(let s) = self { s } else { nil } }
    public subscript(key: String) -> JSONValue? { if case .object(let o) = self { o[key] } else { nil } }

    /// Re-decodes this value as a typed model (a run's `output` read as a recall answer).
    public func decode<T: Decodable>(_ type: T.Type) throws -> T {
        try JSONDecoder().decode(T.self, from: try JSONEncoder().encode(self))
    }
}

/// `GET /api/gateway/me` answers `{ok, principal: {user_id, tenant_id, roles, ...}, auth, routing}`
/// (verified live 2026-10-16); older shapes put the fields at the top level, so both are read.
public struct Principal: Codable, Hashable, Sendable {
    public var userId: String?
    public var tenantId: String?
    public var roles: [String]?
    public var admin: Bool?
    enum CodingKeys: String, CodingKey { case userId = "user_id", tenantId = "tenant_id", roles, principal, admin }
    public init(userId: String?, tenantId: String?, roles: [String]?) { self.userId = userId; self.tenantId = tenantId; self.roles = roles }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let inner = try? c.nestedContainer(keyedBy: CodingKeys.self, forKey: .principal) {
            userId = try inner.decodeIfPresent(String.self, forKey: .userId)
            tenantId = try inner.decodeIfPresent(String.self, forKey: .tenantId)
            roles = try inner.decodeIfPresent([String].self, forKey: .roles)
            admin = try inner.decodeIfPresent(Bool.self, forKey: .admin)
        } else {
            userId = try c.decodeIfPresent(String.self, forKey: .userId)
            tenantId = try c.decodeIfPresent(String.self, forKey: .tenantId)
            roles = try c.decodeIfPresent([String].self, forKey: .roles)
        }
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(userId, forKey: .userId); try c.encodeIfPresent(tenantId, forKey: .tenantId); try c.encodeIfPresent(roles, forKey: .roles)
    }
}

public struct StartRunResponse: Codable, Sendable {
    public var runId: String
    enum CodingKeys: String, CodingKey { case runId = "run_id" }
}

/// A run's `waiting` block: why it stopped (`user`, `subworkflow`, `until`, `event`, `job`).
public struct RunWaiting: Codable, Hashable, Sendable {
    public var reason: String?
    public var waitKey: String?
    enum CodingKeys: String, CodingKey { case reason, waitKey = "wait_key" }
    public init(reason: String?, waitKey: String? = nil) { self.reason = reason; self.waitKey = waitKey }
}

/// `GET /api/gateway/runs/{id}` (`run_summary`), the fields a note client reads.
public struct RunSummary: Codable, Hashable, Sendable, Identifiable {
    public var runId: String
    public var workflowId: String?
    public var status: String
    public var sessionId: String?
    public var error: String?
    public var waiting: RunWaiting?
    public var output: JSONValue?

    public var id: String { runId }
    public var isTerminal: Bool { ["completed", "failed", "cancelled"].contains(status) }
    /// Stopped on a person (an approval or a question): the watch cannot answer it.
    public var waitsOnPerson: Bool { status == "waiting" && waiting?.reason == "user" }

    public init(runId: String, workflowId: String? = nil, status: String, sessionId: String? = nil, error: String? = nil,
                waiting: RunWaiting? = nil, output: JSONValue? = nil) {
        self.runId = runId; self.workflowId = workflowId; self.status = status; self.sessionId = sessionId
        self.error = error; self.waiting = waiting; self.output = output
    }

    enum CodingKeys: String, CodingKey {
        case runId = "run_id", workflowId = "workflow_id", status, sessionId = "session_id", error, waiting, output
    }
}

/// The gateway's error body: FastAPI's `{"detail": "text"}` or the envelope
/// `{"detail": {"reason_code", "message"}}` (abstractgateway automation_errors.py).
public struct GatewayErrorBody: Decodable, Sendable, Equatable {
    public var message: String?
    public var reasonCode: String?
    enum Keys: String, CodingKey { case detail }
    enum Inner: String, CodingKey { case message, reasonCode = "reason_code" }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        if let text = try? c.decode(String.self, forKey: .detail) { message = text; reasonCode = nil; return }
        let inner = try c.nestedContainer(keyedBy: Inner.self, forKey: .detail)
        message = try inner.decodeIfPresent(String.self, forKey: .message)
        reasonCode = try inner.decodeIfPresent(String.self, forKey: .reasonCode)
    }
    public static func parse(_ body: String) -> GatewayErrorBody? { try? JSONDecoder().decode(Self.self, from: Data(body.utf8)) }
}

/// ISO 8601 times from the gateway, with or without fractional seconds.
public enum ISO8601 {
    public static func date(_ s: String) -> Date? {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = f.date(from: s) { return d }
        f.formatOptions = [.withInternetDateTime]
        return f.date(from: s)
    }
    public static func string(_ d: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.string(from: d)
    }
}
