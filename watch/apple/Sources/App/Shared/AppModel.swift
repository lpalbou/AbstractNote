import AbstractGatewayClient
import AbstractWatchKit
import Foundation
import SwiftUI

enum Tab: Hashable { case note, ask, today, settings }

/// Pages opened from a screen.
enum Route: Hashable {
    case loop(OpenLoop)
    case cue(Cue)
    case note(id: String, title: String?)
    /// The notes behind a cue or an insight.
    case why(title: String, noteIds: [String])
    case about
    /// An entity's page: card, how it evolved, notes.
    case entity(id: String, name: String)
    /// A theme (community) and its main entities.
    case theme(Community)
    /// Settings → Server: the editable address.
    case server
    /// Settings → Account: sign in (as someone else).
    case signIn
}

/// What Today shows: read together, kept until the next visit's read (the cursor moves on each
/// successful read, so a second read would show only what came after it).
struct TodayData: Equatable {
    var changes: NoteChanges
    var cues: [Cue]
    var themes: [Community]
    var loops: [OpenLoop]
}

/// The app's state: who is signed in where, the client, the open tab, what Today read last.
@MainActor
final class AppModel: ObservableObject {
    static let appName = "AbstractNote"
    /// The sample notebook's server, as Settings and the error sentences name it.
    static let mockHost = "notes.example.org"
    static let cursorKey = "changes_cursor"

    @Published private(set) var client: any GatewayClient
    @Published var needsSignIn: Bool
    /// Why the sign-in page is back, when it is not the first launch.
    @Published var signInNotice: String?
    @Published var tab: Tab = .note
    @Published var notePath: [Route] = []
    @Published var todayPath: [Route] = []
    @Published var settingsPath: [Route] = []
    @Published var askPath: [Route] = []
    /// "Add a note about this": the Note screen opens with this subject.
    @Published var subject: NoteSubject?
    @Published var today: TodayData?
    @Published private(set) var signIn: SavedSignIn?
    /// The account is being read (`GET /me`) after a sign-in that did not name it.
    @Published private(set) var checkingAccount = false
    let location: Location
    /// The screenshot or UI-test scene to play (`--screen`), mock runs only.
    var demo: String?
    /// `--test-identify` / `--test-secret` (debug builds): sign in without typing.
    var testIdentify: String?
    var testSecret: String?
    private let file: CredentialFile?

    init(client: any GatewayClient, signIn: SavedSignIn?, file: CredentialFile?, needsSignIn: Bool, location: Location) {
        self.client = client; self.signIn = signIn; self.file = file; self.needsSignIn = needsSignIn; self.location = location
    }

    var isMock: Bool { client.isMock }
    var gatewayHost: String { signIn?.gateway.host() ?? (isMock ? Self.mockHost : "—") }
    var account: String? { signIn?.user ?? (isMock ? "laurent" : nil) }
    /// The watch's conversation for its notes and questions.
    var sessionId: String { signIn?.sessionId ?? "note-watch-mock" }
    var changesCursor: String? { signIn?.appState[Self.cursorKey] }

    /// Signed in (the sign-in page): saves the sealed file, replaces the client, clears what the
    /// last account read.
    func connect(_ s: SavedSignIn) throws {
        try file?.save(s)
        SignInDefaults.remember(s)
        signIn = s
        client = LiveGatewayClient(baseURL: s.gateway, credential: s.credential)
        today = nil; subject = nil; notePath = []; todayPath = []; settingsPath = []; askPath = []
        needsSignIn = false; signInNotice = nil; tab = .note
        ensureProcessor()
        Task { await ensureAccount() }
    }

    /// The repair of the user's processor and reflect automations, started once per launch and
    /// after each sign-in (`POST /note/processor/ensure`): after a release they run a workflow
    /// version the server no longer serves, and a watch-only user has no other way to repair them.
    /// Fire-and-forget: every failure is ignored (503 when the note workflows are not loaded, no
    /// connection: the next launch tries again) except a 401, which shows the sign-in.
    private var ensureTask: Task<Void, Never>?

    func ensureProcessor() {
        let client = self.client
        ensureTask = Task { [weak self] in
            do { _ = try await client.ensureProcessor() }
            catch GatewayError.unauthorized { self?.signOut(notice: "Sign in to your notebook.") }
            catch { /* ignored, see above */ }
        }
    }

    /// A capture waits for the repair started at launch or sign-in, so the first note is not
    /// queued behind a stopped processor.
    func processorReady() async { await ensureTask?.value }

    /// Forgets the credential (the file is deleted; ADR 0006), keeps the server as the next
    /// sign-in's default, and shows the sign-in page.
    func signOut(notice: String? = nil) {
        let old = client
        Task { await old.signOut() }
        if let s = signIn { SignInDefaults.remember(s) }
        do { try file?.delete() } catch { signInNotice = "The saved sign-in could not be removed." }
        signIn = nil
        client = MockGatewayClient(failure: .unauthorized)
        today = nil; subject = nil; notePath = []; todayPath = []; settingsPath = []; askPath = []
        signInNotice = notice; needsSignIn = true
    }

    /// A sign-in made by launch arguments (or an older file) may not name the account: read it
    /// from `GET /api/gateway/me` and keep it, so Settings shows who is signed in.
    func ensureAccount() async {
        guard var s = signIn, s.user == nil, !isMock else { return }
        checkingAccount = true
        defer { checkingAccount = false }
        guard let me = try? await client.me(), let id = me.userId else { return }
        s.user = id
        try? file?.save(s)
        SignInDefaults.remember(s)
        signIn = s
    }

    /// Today read: the next visit starts from this cursor.
    func keep(_ data: TodayData) {
        today = data
        guard var s = signIn else { return }
        s.appState[Self.cursorKey] = data.changes.cursor
        do { try file?.save(s); signIn = s } catch { /* the cursor stays; the next visit repeats some news */ }
    }

    /// "Add a note about this": opens the Note screen about `subject`.
    func addNote(about subject: NoteSubject) {
        self.subject = subject
        notePath = []
        tab = .note
    }

    /// A failure as one specific sentence (the operator's rule): signed out, AbstractNote not set
    /// up (a 404 checked against `GET /note/status`), unreachable, or the server's own sentence.
    func problem(_ error: Error) async -> Problem {
        let p = Problem.of(error, host: gatewayHost)
        guard case .notFound = p else { return p }
        do {
            try await client.noteStatus()
            return p
        } catch GatewayError.http(404, _) {
            return .notSetUp(app: Self.appName)
        } catch {
            return Problem.of(error, host: gatewayHost)
        }
    }

    /// "Sign in" on a failure: the sign-in page, the server kept.
    func signInAgain() { signOut(notice: nil) }
}

/// Launch arguments: `--mock` (sample notebook), `--screen <name>` (open on a screen, for
/// screenshots), `--gateway URL --token T [--user U]` (provision a sign-in from the Mac with
/// devicectl), `--forget-gateway` (sign out). With none, the first screen is the sign-in. See BUILD.md.
struct LaunchOptions {
    static let screens = ["signin", "note", "note-typed", "note-listening", "note-recording", "note-no-stt", "note-sending", "note-organizing",
                          "note-saved", "note-error", "entity",
                          "ask", "ask-quick", "ask-thinking", "ask-answer", "ask-deeper",
                          "today", "today-quiet", "today-signin", "today-notsetup", "today-offline",
                          "theme", "loop", "cue", "cue-why", "note-detail", "settings", "server", "account", "about"]

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
            // The scene decides how the sample notebook behaves: still answering, failing, quiet.
            let s = screen ?? "note"
            let failure: MockGatewayClient.Failure? = switch s {
            case "note-error", "today-offline": .offline
            case "today-signin": .unauthorized
            case "today-notsetup": .notSetUp
            default: nil
            }
            let client = MockGatewayClient(answerDelay: ["note-sending", "ask-thinking"].contains(s) ? .seconds(3600) : .milliseconds(600),
                                           failure: failure, quiet: s == "today-quiet", canTranscribe: s != "note-no-stt",
                                           organizeAfter: s == "note-organizing" ? .seconds(3600) : .milliseconds(1800))
            let model = AppModel(client: client, signIn: nil, file: nil, needsSignIn: s == "signin", location: location)
            model.demo = s
            return model
        }
        var file: CredentialFile?
        var saved: SavedSignIn?
        var notice: String?
        do {
            let f = try CredentialFile.standard(folder: AppModel.appName)
            file = f
            if args.contains("--forget-gateway") { try f.delete() }
            // A sign-in passed on the command line is kept: `devicectl device process launch … --gateway U --token T`
            // provisions a watch once from the Mac, without typing on the wrist. The account is read
            // from GET /me right after (ensureAccount) when --user is not given.
            if let gw = value("--gateway").flatMap(SavedSignIn.normalize), let token = value("--token") {
                try f.save(SavedSignIn(gateway: gw, user: value("--user"), credential: .token(token), sessionId: NoteFlows.newSessionId()))
            }
            saved = try f.load()
            // The sign-in screen (Settings → Account, or after Sign out) starts from this server.
            if let saved { SignInDefaults.remember(saved) }
        } catch {
            notice = "Your sign-in could not be read. Please sign in again."
        }
        let client: any GatewayClient = saved.map { LiveGatewayClient(baseURL: $0.gateway, credential: $0.credential) }
            ?? MockGatewayClient(failure: .unauthorized)
        let model = AppModel(client: client, signIn: saved, file: file, needsSignIn: saved == nil, location: location)
        model.signInNotice = notice
        #if DEBUG
        model.testIdentify = value("--test-identify"); model.testSecret = value("--test-secret")
        #endif
        return model
    }
}
