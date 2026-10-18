import AbstractGatewayClient
import SwiftUI

/// What changed since you last looked, what the notebook noticed for you, your open loops, a few
/// prompts, and what you wrote on this day. Nothing changes without a tap: acknowledging and
/// closing are on each item's page.
struct TodayScreen: View {
    @EnvironmentObject private var model: AppModel
    @State private var loading = false
    @State private var error: String?
    @State private var demoPlayed = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 6) {
                if let data = model.today {
                    content(data)
                } else if let error {
                    ErrorLine(text: error)
                    Button("Try again") { Task { await load() } }
                        .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton)).padding(.top, 6)
                } else {
                    // The spinner stays through the client's retries (ADR-0039 in abstractframework).
                    VStack(spacing: 8) { ProgressView(); LoadingText().font(.rounded(.footnote)).foregroundStyle(Theme.muted) }
                        .frame(maxWidth: .infinity).padding(.top, 30)
                }
            }
            .padding(.horizontal, 2)
        }
        .screen("Today")
        .task { if model.today == nil { await load() }; await playDemo() }
    }

    @ViewBuilder private func content(_ d: TodayData) -> some View {
        let c = d.changes
        SectionTitle(text: "Since you last looked")
        if c.isEmpty {
            Text("Nothing new since your last look.").font(.rounded(.footnote)).foregroundStyle(Theme.text2)
        } else {
            ForEach(c.notes) { n in row(n.title ?? "A new note", detail: "New note", symbol: "square.and.pencil", route: .note(id: n.noteId, title: n.title)) }
            ForEach(c.entities) { e in row(e.name, detail: "New in your notes", symbol: "person.crop.circle.badge.plus", route: nil) }
            ForEach(c.supersededClaims) { cl in row(cl.text ?? "A belief", detail: "No longer true", symbol: "arrow.triangle.2.circlepath", route: nil) }
            ForEach(c.closedLoops) { l in row(l.text, detail: "Done", symbol: "checkmark.circle", route: nil) }
            ForEach(c.failedNotes) { n in row(n.title ?? "A note", detail: "Could not be filed yet", symbol: "exclamationmark.triangle", route: .note(id: n.noteId, title: n.title)) }
        }
        let insights = d.insights + c.insights.filter { i in !d.insights.contains { $0.noteId == i.noteId } }
        if !insights.isEmpty {
            SectionTitle(text: "Noticed for you")
            ForEach(insights) { i in row(i.title, detail: nil, symbol: "lightbulb", route: .insight(i)) }
        }
        if !d.loops.isEmpty {
            SectionTitle(text: "Open loops")
            ForEach(d.loops) { l in row(l.text, detail: When.short(l.due).map { "Due \($0)" }, symbol: "circle", route: .loop(l)) }
        }
        if !d.cues.isEmpty {
            SectionTitle(text: "Worth a note")
            ForEach(d.cues) { cue in row(cue.text, detail: nil, symbol: "text.bubble", route: .cue(cue)) }
        }
        if !d.onThisDay.isEmpty {
            SectionTitle(text: "On this day")
            ForEach(d.onThisDay) { n in row(n.title ?? "A note", detail: When.short(n.occurredAt), symbol: "calendar", route: .note(id: n.noteId, title: n.title)) }
        }
        if let error { ErrorLine(text: error).padding(.top, 6) }
        Button { Task { await load() } } label: {
            if loading { ProgressView() } else { Text("Refresh") }
        }
        .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton)).padding(.top, 8)
        .disabled(loading)
        .accessibilityHint("Shows what changed since now")
    }

    /// A line: an icon (never colour alone), the text, a small detail; a chevron when it opens.
    @ViewBuilder private func row(_ text: String, detail: String?, symbol: String, route: Route?) -> some View {
        let label = Card {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: symbol).font(.caption).foregroundStyle(Theme.cyan).frame(width: 16).padding(.top, 2)
                VStack(alignment: .leading, spacing: 1) {
                    Text(text).font(.rounded(.footnote)).foregroundStyle(Theme.text).lineLimit(3)
                    if let detail { Text(detail).font(.rounded(.caption2)).foregroundStyle(Theme.muted) }
                }
                Spacer(minLength: 0)
                if route != nil { Image(systemName: "chevron.right").font(.caption2).foregroundStyle(Theme.muted).padding(.top, 2) }
            }
        }
        .accessibilityElement(children: .combine)
        if let route {
            NavigationLink(value: route) { label }.buttonStyle(.plain)
        } else {
            label
        }
    }

    /// The five reads together. The cursor moves on only once they are in.
    private func load() async {
        loading = true; error = nil
        defer { loading = false }
        let client = model.client
        let cursor = model.signIn?.changesCursor
        do {
            async let changes = client.changes(since: cursor)
            async let insights = client.insights(limit: 5)
            async let loops = client.openLoops(limit: 5)
            async let cues = client.cues(limit: 3)
            async let overview = client.overview()
            let data = TodayData(changes: try await changes, insights: try await insights, loops: try await loops,
                                 cues: try await cues, onThisDay: try await overview.resurfacing)
            model.keep(data)
        } catch {
            self.error = model.say(error)
        }
    }

    /// Screenshot scenes (`--mock --screen insight|loop|cue|note-detail`): open the first item.
    private func playDemo() async {
        guard model.isMock, !demoPlayed, let demo = model.demo, let d = model.today else { return }
        demoPlayed = true
        switch demo {
        case "insight": if let i = d.insights.first { model.todayPath = [.insight(i)] }
        case "loop": if let l = d.loops.first { model.todayPath = [.loop(l)] }
        case "cue": if let c = d.cues.first { model.todayPath = [.cue(c)] }
        case "note-detail": model.todayPath = [.note(id: MockGatewayClient.evidence[0].noteId, title: nil)]
        default: break
        }
    }
}
