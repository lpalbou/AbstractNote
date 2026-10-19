import AbstractGatewayClient
import AbstractWatchKit
import SwiftUI

/// Note for Apple Watch: write a note (Note), ask your notes (Ask), see what changed (Today),
/// Settings. Four pages, swiped vertically with the Digital Crown.
@main
struct AbstractNoteWatchApp: App {
    @StateObject private var model: AppModel

    init() {
        let options = LaunchOptions.parse()
        let model = options.makeModel()
        switch options.screen ?? "" {
        case let s where s.hasPrefix("ask"): model.tab = .ask
        case let s where s.hasPrefix("today") || ["insight", "loop", "cue", "cue-why", "note-detail"].contains(s): model.tab = .today
        case "settings", "about", "server", "account": model.tab = .settings
        default: model.tab = .note
        }
        if model.isMock {
            switch options.screen {
            case "about": model.settingsPath = [.about]
            case "server": model.settingsPath = [.server]
            case "account": model.settingsPath = [.signIn]
            default: break
            }
        }
        // Once per launch, signed in: repair the processor and reflect automations (AppModel.ensureProcessor).
        if !model.needsSignIn { model.ensureProcessor() }
        _model = StateObject(wrappedValue: model)
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if model.needsSignIn {
                    NoteSignIn()
                } else {
                    TabView(selection: $model.tab) {
                        NavigationStack { NoteScreen() }
                            .tag(Tab.note)
                        NavigationStack(path: $model.askPath) {
                            AskScreen().navigationDestination(for: Route.self) { RouteView(route: $0) }
                        }
                        .tag(Tab.ask)
                        NavigationStack(path: $model.todayPath) {
                            TodayScreen().navigationDestination(for: Route.self) { RouteView(route: $0) }
                        }
                        .tag(Tab.today)
                        NavigationStack(path: $model.settingsPath) {
                            SettingsScreen().navigationDestination(for: Route.self) { RouteView(route: $0) }
                        }
                        .tag(Tab.settings)
                    }
                    .tabViewStyle(.verticalPage)
                    .task { await model.ensureAccount() }
                }
            }
            .environmentObject(model)
            .environmentObject(model.location)
            .tint(Theme.indigo)
            .preferredColorScheme(.dark)
        }
    }
}

struct RouteView: View {
    let route: Route
    var body: some View {
        switch route {
        case .insight(let i): InsightView(insight: i)
        case .loop(let l): LoopView(loop: l)
        case .cue(let c): CueView(cue: c)
        case .note(let id, let title): NoteDetailView(noteId: id, title: title)
        case .why(let title, let ids): WhyView(title: title, noteIds: ids)
        case .about: AboutView()
        case .server: NoteServerView()
        case .signIn: NoteSignIn()
        }
    }
}

/// The kit's sign-in with AbstractNote's name, mark and sealed sign-in file.
struct NoteSignIn: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        SignInView(appName: AppModel.appName, notice: model.signInNotice, testIdentify: model.testIdentify, testSecret: model.testSecret,
                   newSessionId: NoteFlows.newSessionId, onSignedIn: { try model.connect($0) }) {
            NoteMark(size: 40)
        }
        .onAppear { model.testIdentify = nil; model.testSecret = nil }
    }
}

/// The kit's server screen: a different server signs out and opens its sign-in.
struct NoteServerView: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        ServerView(current: model.signIn?.gateway ?? (model.isMock ? URL(string: "https://\(AppModel.mockHost)") : nil)) { url in
            model.signOut(notice: "Sign in to \(url.host() ?? url.absoluteString).")
        }
    }
}
