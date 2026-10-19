import Foundation

/// What went wrong, as the person can act on it: one sentence, and the one action that helps.
/// The operator's rule (2026-10-19): never a vague "That didn't work"; say not signed in, not set
/// up, not reachable, or the server's own sentence.
public enum Problem: Equatable, Sendable {
    /// 401: "Sign in to your notebook." with a Sign in button.
    case signIn
    /// No answer from the server: "Can't reach <host>." with Try again.
    case unreachable(host: String)
    /// The app's part of the server is missing (the app checked its status route: 404).
    case notSetUp(app: String)
    /// A 404 the app has not explained yet (it probes its status route to tell "not set up" apart).
    case notFound(String?)
    /// Anything else: the server's own sentence, or the app's.
    case said(String)

    public enum Action: Sendable { case signIn, retry, none }

    public var sentence: String {
        switch self {
        case .signIn: "Sign in to your notebook."
        case .unreachable(let host): "Can't reach \(host)."
        case .notSetUp(let app): "\(app) isn't set up on this server yet."
        case .notFound(let s): s ?? "That isn't on the server."
        case .said(let s): s
        }
    }

    public var action: Action {
        switch self {
        case .signIn: .signIn
        case .unreachable, .said, .notFound: .retry
        case .notSetUp: .none
        }
    }

    /// Sorts a failure. `host` names the server in "Can't reach …".
    public static func of(_ error: Error, host: String) -> Problem {
        switch error {
        case GatewayError.unauthorized: return .signIn
        case let e as URLError where e.code == .cancelled: return .said("Stopped.")
        case is URLError, GatewayError.invalidResponse: return .unreachable(host: host)
        case GatewayError.http(404, let body): return .notFound(GatewayErrorBody.parse(body)?.message.flatMap(sentence))
        case GatewayError.http(let status, let body):
            return .said(GatewayErrorBody.parse(body)?.message.flatMap(sentence)
                         ?? (status >= 500 ? "The server is having trouble. Try again." : "The server refused this. Try again."))
        case GatewayError.conflict(let body): return .said(GatewayErrorBody.parse(body)?.message.flatMap(sentence) ?? "Already done on another device.")
        case let e as GatewayError: return .said(e.errorDescription ?? "Something went wrong. Try again.")
        case let e as GatewaySignIn.Failure: return .said(e.errorDescription ?? "Sign-in failed. Try again.")
        default: return .said("Something went wrong. Try again.")
        }
    }

    /// A server's message as a sentence for people: trimmed, without the runtime's "Runtime error: "
    /// prefix; nil when empty.
    public static func sentence(_ text: String?) -> String? {
        guard var t = text?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        let prefix = "Runtime error: "
        if t.hasPrefix(prefix) { t = String(t.dropFirst(prefix.count)).trimmingCharacters(in: .whitespaces) }
        return t.isEmpty ? nil : t
    }
}
