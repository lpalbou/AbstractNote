import Foundation
import Testing
@testable import GatewayCore

/// ADR 0006: one file, Data Protection until first unlock, no backup, owner-only, gone on sign-out,
/// never in UserDefaults.
@Suite struct CredentialFileTests {
    private func tempFile() -> URL {
        FileManager.default.temporaryDirectory
            .appending(path: "abstractnote-cred-\(UUID().uuidString)", directoryHint: .isDirectory)
            .appending(path: "signin.json")
    }

    private let signIn = SavedSignIn(gateway: URL(string: "https://gateway.example")!, user: "alice",
                                     credential: .token("agw_secret_\(UUID().uuidString)"), sessionId: "note-watch-1", appState: ["changes_cursor": "c-1"])

    static func backupExcluded(_ url: URL) -> Bool {
        getxattr(url.path(percentEncoded: false), "com.apple.metadata:com_apple_backup_excludeItem", nil, 0, 0, 0) > 0
    }

    final class Recorder: @unchecked Sendable { var options: [Data.WritingOptions] = [] }

    @Test func theFileIsWrittenWithDataProtectionUntilFirstUnlockAndAtomically() throws {
        let rec = Recorder()
        let file = CredentialFile(url: tempFile(), writer: .init { data, url, options in
            rec.options.append(options)
            try data.write(to: url, options: options)
        })
        try file.save(signIn)
        let opts = try #require(rec.options.first)
        #expect(opts.contains(.completeFileProtectionUntilFirstUserAuthentication))
        #expect(opts.contains(.atomic))
        #expect(CredentialFile.protection == .completeUntilFirstUserAuthentication)
        try file.delete()
    }

    @Test func theFileIsOwnerOnlyAndExcludedFromBackup() throws {
        let file = CredentialFile(url: tempFile())
        try file.save(signIn)
        try file.save(signIn)   // a second (atomic) write keeps the attributes
        let attrs = try FileManager.default.attributesOfItem(atPath: file.url.path(percentEncoded: false))
        #expect((attrs[.posixPermissions] as? NSNumber)?.intValue == 0o600)
        // The backup exclusion is the item's own attribute (the temporary folder reads as excluded
        // anyway, so `isExcludedFromBackup` alone would not see it missing).
        #expect(Self.backupExcluded(file.url))
        #expect(Self.backupExcluded(file.url.deletingLastPathComponent()))
        // Where the volume reports a protection class (a device), it is the one asked for.
        if let p = attrs[.protectionKey] as? FileProtectionType { #expect(p == .completeUntilFirstUserAuthentication) }
        try file.delete()
    }

    @Test func itRoundTripsAndSignOutDeletesIt() throws {
        let file = CredentialFile(url: tempFile())
        #expect(try file.load() == nil)
        try file.save(signIn)
        #expect(try file.load() == signIn)
        var session = signIn; session.credential = .session(id: "s1", csrf: "c1")
        try file.save(session)
        #expect(try file.load() == session)
        try file.delete()
        #expect(!FileManager.default.fileExists(atPath: file.url.path(percentEncoded: false)))
        #expect(try file.load() == nil)
    }

    @Test func anUnreadableFileIsRemovedAndSaidSo() throws {
        let file = CredentialFile(url: tempFile())
        try file.save(signIn)
        try Data(#"{"version":9}"#.utf8).write(to: file.url)
        #expect(throws: (any Error).self) { try file.load() }
        #expect(!FileManager.default.fileExists(atPath: file.url.path(percentEncoded: false)))
    }

    @Test func theSecretNeverReachesUserDefaults() throws {
        let file = CredentialFile(url: tempFile())
        try file.save(signIn)
        guard case .token(let secret) = signIn.credential else { return }
        let all = UserDefaults.standard.dictionaryRepresentation().values.map { "\($0)" }
        #expect(!all.contains { $0.contains(secret) })
        #expect(String(decoding: try Data(contentsOf: file.url), as: UTF8.self).contains(secret))
        try file.delete()
    }

    @Test func serverAddressesAreNormalized() {
        #expect(SavedSignIn.normalize("gateway.example.org/")?.absoluteString == "https://gateway.example.org")
        #expect(SavedSignIn.normalize("http://192.168.1.4:8080")?.absoluteString == "http://192.168.1.4:8080")
        #expect(SavedSignIn.normalize("ftp://x") == nil && SavedSignIn.normalize("  ") == nil)
    }
}
