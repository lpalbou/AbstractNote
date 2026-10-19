import AbstractGatewayClient
import AbstractWatchKit
import SwiftUI
import WatchKit

/// Today, at a glance: what changed since you last looked (counts, one line each), one thought
/// worth a note at a time (Answer or Skip), the five largest themes, and your open loops with
/// Close. Nothing changes without a tap.
struct TodayScreen: View {
    @EnvironmentObject private var model: AppModel
    @State private var loading = false
    @State private var problem: Problem?
    /// Cues skipped on this visit (only on this watch, until the next read).
    @State private var skipped: Set<String> = []
    @State private var closing: String?
    @State private var demoPlayed = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.s) {
                if let data = model.today {
                    content(data)
                } else if let problem {
                    ProblemView(problem: problem, signIn: model.signInAgain, retry: { Task { await load() } })
                        .padding(.top, Space.s)
                } else {
                    // The spinner stays through the client's retries (ADR-0039 in abstractframework).
                    VStack(spacing: Space.s) { ProgressView(); LoadingText().font(.rounded(.footnote)).foregroundStyle(Theme.muted) }
                        .frame(maxWidth: .infinity).padding(.top, Space.l)
                }
            }
            .padding(.horizontal, Space.xs)
        }
        .screen("Today")
        .task { if model.today == nil { await load() }; await playDemo() }
    }

    @ViewBuilder private func content(_ d: TodayData) -> some View {
        sinceYouLooked(d.changes)
        worthAThought(d.cues)
        themes(d.themes)
        loops(d.loops)
        if let problem { ErrorLine(text: problem.sentence).padding(.top, Space.s) }
        Button { Task { await load() } } label: {
            if loading { ProgressView() } else { Text("Refresh") }
        }
        .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton)).padding(.top, Space.m)
        .disabled(loading)
        .accessibilityHint("Shows what changed since now")
    }

    // MARK: - Since you last looked

    @ViewBuilder private func sinceYouLooked(_ c: NoteChanges) -> some View {
        SectionTitle(text: "Since you last looked")
        Card {
            VStack(alignment: .leading, spacing: Space.s) {
                if c.isEmpty {
                    Text("Nothing new since your last look.").font(.rounded(.footnote)).foregroundStyle(Theme.text2)
                } else {
                    count(c.notes.count, "new note", "new notes", symbol: "square.and.pencil")
                    count(c.entities.count, "new person or thing", "new people and things", symbol: "person.crop.circle.badge.plus")
                    count(c.supersededClaims.count, "belief changed", "beliefs changed", symbol: "arrow.triangle.2.circlepath")
                    count(c.closedLoops.count, "loop closed", "loops closed", symbol: "checkmark.circle")
                    count(c.insights.count, "new insight", "new insights", symbol: "lightbulb")
                    count(c.failedNotes.count, "note needs attention", "notes need attention", symbol: "exclamationmark.triangle")
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private func count(_ n: Int, _ one: String, _ many: String, symbol: String) -> some View {
        if n > 0 {
            Label { Text("\(n) \(n == 1 ? one : many)").foregroundStyle(Theme.text) } icon: { Image(systemName: symbol).foregroundStyle(Theme.indigo) }
                .font(.rounded(.footnote))
                .lineLimit(1).minimumScaleFactor(0.8)
        }
    }

    // MARK: - Worth a thought

    @ViewBuilder private func worthAThought(_ cues: [Cue]) -> some View {
        let left = cues.filter { !skipped.contains($0.cueId) }
        if !cues.isEmpty {
            SectionTitle(text: "Worth a thought").padding(.top, Space.s)
            if let cue = left.first {
                Card {
                    VStack(alignment: .leading, spacing: Space.s) {
                        NavigationLink(value: Route.cue(cue)) {
                            Text(cue.text).font(.rounded(.body)).foregroundStyle(Theme.text)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .buttonStyle(.plain)
                        .accessibilityHint("Shows why")
                        if left.count > 1 {
                            Text("1 of \(left.count)").font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                        }
                        HStack(spacing: Space.s) {
                            Button("Answer") { model.addNote(about: NoteSubject(kind: "cue", id: cue.cueId, title: cue.text)) }
                                .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                                .accessibilityHint("Writes a note about this")
                                .accessibilityIdentifier("cue.answer")
                            Button("Skip") { withAnimation(.easeOut(duration: 0.15)) { _ = skipped.insert(cue.cueId) } }
                                .buttonStyle(FilledButtonStyle(fill: Theme.raised))
                                .accessibilityHint("Shows the next one")
                                .accessibilityIdentifier("cue.skip")
                        }
                    }
                }
            } else {
                Text("No more for today.").font(.rounded(.footnote)).foregroundStyle(Theme.text2)
            }
        }
    }

    // MARK: - Themes

    @ViewBuilder private func themes(_ items: [Community]) -> some View {
        if !items.isEmpty {
            SectionTitle(text: "Themes").padding(.top, Space.s)
            ForEach(items.prefix(5)) { c in
                NavigationLink(value: Route.theme(c)) {
                    row(title: c.label.shown ?? "Not named yet", detail: c.size == 1 ? "1 entry" : "\(c.size) entries",
                        symbol: "circle.hexagongrid", muted: c.label.shown == nil)
                }
                .buttonStyle(.plain)
                .accessibilityHint("Shows what it gathers")
            }
        }
    }

    // MARK: - Open loops

    @ViewBuilder private func loops(_ items: [OpenLoop]) -> some View {
        if !items.isEmpty {
            SectionTitle(text: "Open loops").padding(.top, Space.s)
            ForEach(items) { l in
                Card {
                    VStack(alignment: .leading, spacing: Space.s) {
                        NavigationLink(value: Route.loop(l)) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(l.text).font(.rounded(.footnote)).foregroundStyle(Theme.text).lineLimit(3)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                if let due = When.short(l.due) { Text("Due \(due)").font(.rounded(.caption2)).foregroundStyle(Theme.muted) }
                            }
                        }
                        .buttonStyle(.plain)
                        Button { Task { await close(l) } } label: {
                            if closing == l.taskId { ProgressView() } else { Label("Close", systemImage: "checkmark") }
                        }
                        .buttonStyle(FilledButtonStyle(fill: Theme.raised))
                        .disabled(closing != nil)
                        .accessibilityLabel("Close: \(l.text)")
                        .accessibilityHint("Marks it done")
                        .accessibilityIdentifier("loop.close.\(l.taskId)")
                    }
                }
            }
        }
    }

    private func row(title: String, detail: String, symbol: String, muted: Bool = false) -> some View {
        HStack(spacing: Space.s) {
            Image(systemName: symbol).font(.footnote).foregroundStyle(Theme.indigo).frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.rounded(.footnote, weight: .medium)).foregroundStyle(muted ? Theme.text2 : Theme.text).lineLimit(2)
                Text(detail).font(.rounded(.caption2)).foregroundStyle(Theme.muted)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(.caption2).foregroundStyle(Theme.muted)
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .accessibilityElement(children: .combine)
    }

    // MARK: - Actions

    private func close(_ l: OpenLoop) async {
        closing = l.taskId
        defer { closing = nil }
        do {
            try await model.client.close(taskId: l.taskId)
        } catch GatewayError.conflict {
            // Already closed elsewhere: the same outcome.
        } catch {
            problem = await model.problem(error); return
        }
        withAnimation { model.today?.loops.removeAll { $0.taskId == l.taskId } }
        WKInterfaceDevice.current().play(.success)
    }

    /// The four reads together. The cursor moves on only once they are in.
    private func load() async {
        loading = true; problem = nil
        defer { loading = false }
        let client = model.client
        let cursor = model.changesCursor
        do {
            async let changes = client.changes(since: cursor)
            async let cues = client.cues(limit: 5)
            async let themes = client.communities()
            async let loops = client.openLoops(limit: 5)
            let data = TodayData(changes: try await changes, cues: try await cues, themes: try await themes.items, loops: try await loops)
            skipped = []
            model.keep(data)
        } catch {
            problem = await model.problem(error)
        }
    }

    /// Screenshot scenes (`--mock --screen theme|loop|cue|cue-why|note-detail`): open the first item.
    private func playDemo() async {
        guard model.isMock, !demoPlayed, let demo = model.demo, let d = model.today else { return }
        demoPlayed = true
        switch demo {
        case "theme": if let c = d.themes.first { model.todayPath = [.theme(c)] }
        case "loop": if let l = d.loops.first { model.todayPath = [.loop(l)] }
        case "cue": if let c = d.cues.first { model.todayPath = [.cue(c)] }
        case "cue-why":
            if let c = d.cues.first, let s = c.subject { model.todayPath = [.cue(c), .why(title: s.title, noteIds: c.evidenceNoteIds)] }
        case "note-detail": model.todayPath = [.note(id: MockGatewayClient.evidence[0].noteId, title: nil)]
        default: break
        }
    }
}
