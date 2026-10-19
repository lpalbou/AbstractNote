import AbstractGatewayClient
import Foundation
import SwiftUI

enum Tab: Hashable { case note, ask, today, settings }

/// Pages opened from a screen.
enum Route: Hashable {
    case insight(Insight)
    case loop(OpenLoop)
    case cue(Cue)
    case note(id: String, title: String?)
    /// The notes behind a cue or an insight.
    case why(title: String, noteIds: [String])
    case about
}

/// What Today shows: read together, kept until the next visit's read (the cursor moves on each
/// successful read, so a second read would show only what came after it).
struct TodayData: Equatable {
    var changes: NoteChanges
    var insights: [Insight]
    var loops: [OpenLoop]
    var cues: [Cue]
    var onThisDay: [NoteRef]
}

/// The app's state: who is signed in where, the client, the open tab, what Today read last.
@MainActor
final class AppModel: ObservableObject {
    @Published private(set) var client: any GatewayClient
    @Published var needsSignIn: Bool
    /// Why the sign-in page is back, when it is not the first launch.
    @Published var signInNotice: String?
    @Published var tab: Tab = .note
    @Published var todayPath: [Route] = []
    @Published var settingsPath: [Route] = []
    @Published var askPath: [Route] = []
    /// "Add a note about this": the Note screen opens with this subject.
    @Published var subject: NoteSubject?
    @Published var today: TodayData?
    @Published private(set) var signIn: SavedSignIn?
    let location: Location
    /// The screenshot or UI-test scene to play (`--screen`), mock runs only.
    var demo: String?
    #if DEBUG
    /// `--test-identify` / `--test-secret`: sign in without typing (simulator and device checks).
    var testIdentify: String?
    var testSecret: String?
    #endif
    private let file: CredentialFile?

    init(client: any GatewayClient, signIn: SavedSignIn?, file: CredentialFile?, needsSignIn: Bool, location: Location) {
        self.client = client; self.signIn = signIn; self.file = file; self.needsSignIn = needsSignIn; self.location = location
    }

    var isMock: Bool { client.isMock }
    var gatewayHost: String { signIn?.gateway.host() ?? (isMock ? "Sample notebook" : "—") }
    var account: String? { signIn?.user ?? (isMock ? "laurent" : nil) }
    /// The watch's conversation for its notes and questions.
    var sessionId: String { signIn?.sessionId ?? "note-watch-mock" }

    /// Signed in (the sign-in page): saves the sealed file, replaces the client, clears what the
    /// last account read.
    func connect(_ s: SavedSignIn) throws {
        try file?.save(s)
        UserDefaults.standard.set(s.gateway.absoluteString, forKey: SignInDefaults.lastGateway)
        UserDefaults.standard.set(s.user, forKey: SignInDefaults.lastUser)
        signIn = s
        client = LiveGatewayClient(baseURL: s.gateway, credential: s.credential)
        today = nil; subject = nil; todayPath = []; settingsPath = []; askPath = []
        needsSignIn = false; signInNotice = nil; tab = .note
    }

    /// Forgets the credential (the file is deleted; ADR 0006) and shows the sign-in page.
    func signOut(notice: String? = nil) {
        let old = client
        Task { await old.signOut() }
        do { try file?.delete() } catch { signInNotice = "The saved sign-in could not be removed." }
        signIn = nil
        client = MockGatewayClient(offline: true)
        today = nil; subject = nil; todayPath = []; settingsPath = []; askPath = []
        signInNotice = notice; needsSignIn = true
    }

    /// Today read: the next visit starts from this cursor.
    func keep(_ data: TodayData) {
        today = data
        guard var s = signIn else { return }
        s.changesCursor = data.changes.cursor
        do { try file?.save(s); signIn = s } catch { /* the cursor stays; the next visit repeats some news */ }
    }

    /// "Add a note about this": opens the Note screen about `subject`.
    func addNote(about subject: NoteSubject) {
        self.subject = subject
        tab = .note
    }

    /// Any failure, in one sentence; a 401 ends the session at once.
    func say(_ error: Error) -> String {
        if case GatewayError.unauthorized = error {
            signOut(notice: "You were signed out. Please sign in again.")
        }
        return Self.say(error)
    }

    nonisolated static func say(_ error: Error) -> String {
        switch error {
        case GatewayError.unauthorized: "You were signed out. Please sign in again."
        case let e as URLError where [.notConnectedToInternet, .networkConnectionLost, .dataNotAllowed].contains(e.code): "No connection."
        case is URLError: "Can't reach your notebook."
        case let e as GatewaySignIn.Failure: e.errorDescription ?? "Sign-in failed. Try again."
        case let e as GatewayError: e.errorDescription ?? "Something went wrong. Try again."
        default: "Something went wrong. Try again."
        }
    }
}

enum SignInDefaults {
    /// Not secret: the last server and account typed, as the next sign-in's defaults.
    static let lastGateway = "signin.lastGateway"
    static let lastUser = "signin.lastUser"
}

/// Launch arguments: `--mock` (sample notebook), `--screen <name>` (open on a screen, for
/// screenshots), `--gateway URL --token T [--user U]` (provision a sign-in from the Mac with
/// devicectl), `--forget-gateway` (sign out). See BUILD.md.
struct LaunchOptions {
    static let screens = ["signin", "note", "note-sending", "note-saved", "note-error", "ask", "ask-thinking", "ask-answer",
                          "ask-deeper", "today", "today-quiet", "insight", "loop", "cue", "cue-why", "note-detail", "settings", "about"]

    var mock: Bool
    var screen: String?
    var args: [String]

    static func parse(_ args: [String] = ProcessInfo.processInfo.arguments) -> LaunchOptions {
        func value(_ flag: String) -> String? {
            guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
            return args[i + 1]
        }
        return LaunchOptions(mock: args.contains("--mock"), screen: value("--screen"), args: args)
    }

    func value(_ flag: String) -> String? {
        guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
        return args[i + 1]
    }

    @MainActor
    func makeModel() -> AppModel {
        let location = Location(quiet: mock)
        if mock {
            // The scene decides how the sample notebook behaves: still answering, unreachable, quiet.
            let s = screen ?? "note"
            let client = MockGatewayClient(answerDelay: ["note-sending", "ask-thinking"].contains(s) ? .seconds(3600) : .milliseconds(600),
                                           offline: s == "note-error", quiet: s == "today-quiet")
            let model = AppModel(client: client, signIn: nil, file: nil, needsSignIn: s == "signin", location: location)
            model.demo = s
            return model
        }
        var file: CredentialFile?
        var saved: SavedSignIn?
        var notice: String?
        do {
            let f = try CredentialFile.standard()
            file = f
            if args.contains("--forget-gateway") { try f.delete() }
            // A sign-in passed on the command line is kept: `devicectl device process launch … --gateway U --token T`
            // provisions a watch once from the Mac, without typing on the wrist.
            if let gw = value("--gateway").flatMap(SavedSignIn.normalize), let token = value("--token") {
                try f.save(SavedSignIn(gateway: gw, user: value("--user"), credential: .token(token)))
            }
            saved = try f.load()
        } catch {
            notice = "Your sign-in could not be read. Please sign in again."
        }
        let client: any GatewayClient = saved.map { LiveGatewayClient(baseURL: $0.gateway, credential: $0.credential) } ?? MockGatewayClient(offline: true)
        let model = AppModel(client: client, signIn: saved, file: file, needsSignIn: saved == nil, location: location)
        model.signInNotice = notice
        #if DEBUG
        model.testIdentify = value("--test-identify"); model.testSecret = value("--test-secret")
        #endif
        return model
    }
}
