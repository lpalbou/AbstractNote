import AbstractGatewayClient
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
        case let s where s.hasPrefix("today") || ["insight", "loop", "cue", "note-detail"].contains(s): model.tab = .today
        case "settings", "about": model.tab = .settings
        default: model.tab = .note
        }
        if model.isMock, options.screen == "about" { model.settingsPath = [.about] }
        _model = StateObject(wrappedValue: model)
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if model.needsSignIn {
                    SignInView()
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
        case .about: AboutView()
        }
    }
}
