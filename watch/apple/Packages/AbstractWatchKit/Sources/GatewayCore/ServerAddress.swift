import Foundation

/// The connection address a person can check, without URL credentials or query secrets.
public enum ServerAddress {
    public static func label(_ url: URL) -> String {
        guard var parts = URLComponents(url: url, resolvingAgainstBaseURL: false), parts.host != nil else {
            return "the server"
        }
        parts.user = nil; parts.password = nil; parts.query = nil; parts.fragment = nil
        let scheme = parts.scheme?.lowercased()
        if (scheme == "https" && parts.port == 443) || (scheme == "http" && parts.port == 80) { parts.port = nil }
        if parts.path == "/" { parts.path = "" }
        guard var value = parts.string else { return "the server" }
        if scheme == "https", value.hasPrefix("https://") { value.removeFirst("https://".count) }
        return value
    }
}
