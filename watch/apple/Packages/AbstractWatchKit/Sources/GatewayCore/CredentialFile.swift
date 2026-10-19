import Foundation

// ADR 0006 (abstractnote docs/adr): the gateway address and the credential live in ONE JSON file
// in the app container, written with Data Protection `completeUntilFirstUserAuthentication`,
// excluded from backup, readable by the owner only, deleted on sign-out. Never UserDefaults for
// the credential, never the keychain (the framework's file-sealed secrets rule). The operator's
// ruling is pending (backlog 0017); this file is the only place to change if it goes the other way.

extension GatewayCredential: Codable {
    enum CodingKeys: String, CodingKey { case kind, token, id, csrf }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        switch try c.decode(String.self, forKey: .kind) {
        case "token": self = .token(try c.decode(String.self, forKey: .token))
        case "session": self = .session(id: try c.decode(String.self, forKey: .id), csrf: try c.decode(String.self, forKey: .csrf))
        case let other: throw DecodingError.dataCorruptedError(forKey: .kind, in: c, debugDescription: "unknown credential kind \(other)")
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .token(let t): try c.encode("token", forKey: .kind); try c.encode(t, forKey: .token)
        case .session(let id, let csrf): try c.encode("session", forKey: .kind); try c.encode(id, forKey: .id); try c.encode(csrf, forKey: .csrf)
        }
    }
}

/// Who is signed in where, and the little the app keeps between visits.
public struct SavedSignIn: Codable, Equatable, Sendable {
    public var gateway: URL
    public var user: String?
    public var credential: GatewayCredential
    /// The watch's conversation id for what it starts (`note-watch-<uuid>` in AbstractNote).
    public var sessionId: String
    /// What the app keeps between visits (AbstractNote: Today's cursor), as plain strings.
    public var appState: [String: String]

    public static let defaultGateway = "https://gateway.abstractframework.ai"

    public init(gateway: URL, user: String?, credential: GatewayCredential, sessionId: String, appState: [String: String] = [:]) {
        self.gateway = gateway; self.user = user; self.credential = credential; self.sessionId = sessionId; self.appState = appState
    }

    enum CodingKeys: String, CodingKey { case version, gateway, user, credential, sessionId = "session_id", appState = "app_state" }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let v = try c.decode(Int.self, forKey: .version)
        guard v == 1 else { throw DecodingError.dataCorruptedError(forKey: .version, in: c, debugDescription: "unknown file version \(v)") }
        gateway = try c.decode(URL.self, forKey: .gateway)
        user = try c.decodeIfPresent(String.self, forKey: .user)
        credential = try c.decode(GatewayCredential.self, forKey: .credential)
        sessionId = try c.decode(String.self, forKey: .sessionId)
        appState = try c.decodeIfPresent([String: String].self, forKey: .appState) ?? [:]
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(1, forKey: .version)
        try c.encode(gateway, forKey: .gateway)
        try c.encodeIfPresent(user, forKey: .user)
        try c.encode(credential, forKey: .credential)
        try c.encode(sessionId, forKey: .sessionId)
        if !appState.isEmpty { try c.encode(appState, forKey: .appState) }
    }

    /// "gateway.example.org" → "https://gateway.example.org"; plain http only when typed.
    public static func normalize(_ text: String) -> URL? {
        var s = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return nil }
        if !s.contains("://") { s = "https://" + s }
        while s.hasSuffix("/") { s.removeLast() }
        guard let u = URL(string: s), let scheme = u.scheme?.lowercased(), scheme == "https" || scheme == "http", u.host() != nil else { return nil }
        return u
    }
}

/// The sealed sign-in file.
public struct CredentialFile: Sendable {
    /// How bytes reach the disk; tests record the options the store asks for.
    public struct Writer: Sendable {
        public var write: @Sendable (Data, URL, Data.WritingOptions) throws -> Void
        public init(write: @escaping @Sendable (Data, URL, Data.WritingOptions) throws -> Void) { self.write = write }
        public static let system = Writer { data, url, options in try data.write(to: url, options: options) }
    }

    /// The Data Protection class of the file: readable after the first unlock since boot, so a
    /// capture started from the wrist-down state still finds its credential.
    public static let protection = FileProtectionType.completeUntilFirstUserAuthentication
    public static let writingOptions: Data.WritingOptions = [.atomic, .completeFileProtectionUntilFirstUserAuthentication]

    public let url: URL
    private let writer: Writer

    public init(url: URL, writer: Writer = .system) { self.url = url; self.writer = writer }

    /// `Application Support/<folder>/signin.json` in the app's own container (AbstractNote: "AbstractNote").
    public static func standard(folder: String) throws -> CredentialFile {
        let base = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        return CredentialFile(url: base.appending(path: folder, directoryHint: .isDirectory).appending(path: "signin.json"))
    }

    /// The saved sign-in; nil when there is none. A file that cannot be read is removed (the person
    /// signs in again) and the reason is thrown, never silently replaced.
    public func load() throws -> SavedSignIn? {
        guard FileManager.default.fileExists(atPath: url.path(percentEncoded: false)) else { return nil }
        do { return try JSONDecoder().decode(SavedSignIn.self, from: try Data(contentsOf: url)) }
        catch { try? FileManager.default.removeItem(at: url); throw error }
    }

    public func save(_ signIn: SavedSignIn) throws {
        let dir = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true,
                                                attributes: [.protectionKey: Self.protection, .posixPermissions: 0o700])
        let enc = JSONEncoder(); enc.outputFormatting = [.sortedKeys]
        try writer.write(try enc.encode(signIn), url, Self.writingOptions)
        // An atomic write replaces the file: its attributes are set again after every write.
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path(percentEncoded: false))
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        var file = url; try file.setResourceValues(values)
        var folder = dir; try folder.setResourceValues(values)
    }

    /// Sign-out: the file is gone.
    public func delete() throws {
        guard FileManager.default.fileExists(atPath: url.path(percentEncoded: false)) else { return }
        try FileManager.default.removeItem(at: url)
    }
}
