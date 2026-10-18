import AbstractGatewayClient
import SwiftUI
import WatchKit

/// Something the notebook noticed: read it, then "Got it" (it leaves Today) or add a note about it.
struct InsightView: View {
    let insight: Insight
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var working = false
    @State private var error: String?

    var body: some View {
        FitScroll {
            VStack(alignment: .leading, spacing: 8) {
                Text(insight.title).font(.rounded(.headline)).foregroundStyle(Theme.text)
                if let body = insight.body { Text(body).font(.rounded(.footnote)).foregroundStyle(Theme.text2) }
                if let d = When.short(insight.occurredAt) { Text(d).font(.rounded(.caption2)).foregroundStyle(Theme.muted) }
                if let error { ErrorLine(text: error) }
                Spacer(minLength: 6)
                Button { Task { await acknowledge() } } label: { if working { ProgressView() } else { Text("Got it") } }
                    .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton)).disabled(working)
                    .accessibilityHint("Removes it from Today")
                    .accessibilityIdentifier("insight.ack")
                Button("Add a note about this") { model.addNote(about: NoteSubject(kind: "insight", id: insight.noteId, title: insight.title)) }
                    .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
            }
            .padding(.horizontal, 2)
        }
        .screen("Noticed")
    }

    private func acknowledge() async {
        working = true; error = nil
        defer { working = false }
        do {
            try await model.client.acknowledge(insightId: insight.noteId)
        } catch GatewayError.conflict {
            // Already acknowledged elsewhere: the same outcome.
        } catch {
            self.error = model.say(error); return
        }
        model.today?.insights.removeAll { $0.noteId == insight.noteId }
        model.today?.changes.insights.removeAll { $0.noteId == insight.noteId }
        WKInterfaceDevice.current().play(.success)
        dismiss()
    }
}

/// An open loop: close it when it is done, or add a note about it.
struct LoopView: View {
    let loop: OpenLoop
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var working = false
    @State private var error: String?

    var body: some View {
        FitScroll {
            VStack(alignment: .leading, spacing: 8) {
                Text(loop.text).font(.rounded(.headline)).foregroundStyle(Theme.text)
                if let d = When.short(loop.due) { Label("Due \(d)", systemImage: "calendar").font(.rounded(.caption2)).foregroundStyle(Theme.muted) }
                if let noteId = loop.noteId {
                    NavigationLink(value: Route.note(id: noteId, title: nil)) {
                        Label("The note it came from", systemImage: "doc.text").font(.rounded(.footnote)).foregroundStyle(Theme.cyan)
                    }
                    .buttonStyle(.plain)
                }
                if let error { ErrorLine(text: error) }
                Spacer(minLength: 6)
                Button { Task { await close() } } label: { if working { ProgressView() } else { Text("Close") } }
                    .buttonStyle(FilledButtonStyle(fill: Theme.greenButton)).disabled(working)
                    .accessibilityLabel("Close this loop")
                    .accessibilityHint("Marks it done")
                    .accessibilityIdentifier("loop.close")
                Button("Add a note about this") { model.addNote(about: NoteSubject(kind: "task", id: loop.taskId, title: loop.text)) }
                    .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
            }
            .padding(.horizontal, 2)
        }
        .screen("Open loop")
    }

    private func close() async {
        working = true; error = nil
        defer { working = false }
        do {
            try await model.client.close(taskId: loop.taskId)
        } catch GatewayError.conflict {
            // Already closed elsewhere: the same outcome.
        } catch {
            self.error = model.say(error); return
        }
        model.today?.loops.removeAll { $0.taskId == loop.taskId }
        WKInterfaceDevice.current().play(.success)
        dismiss()
    }
}

/// A prompt from the notebook, answered by writing a note about it.
struct CueView: View {
    let cue: Cue
    @EnvironmentObject private var model: AppModel

    var body: some View {
        FitScroll {
            VStack(alignment: .leading, spacing: 8) {
                Text(cue.text).font(.rounded(.headline)).foregroundStyle(Theme.text)
                Spacer(minLength: 6)
                Button("Add a note") { model.addNote(about: NoteSubject(kind: "cue", id: cue.cueId, title: cue.text)) }
                    .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                    .accessibilityHint("Opens a new note about this")
            }
            .padding(.horizontal, 2)
        }
        .screen("Worth a note")
    }
}

/// One note: its words and when it was written.
struct NoteDetailView: View {
    let noteId: String
    let title: String?
    @EnvironmentObject private var model: AppModel
    @State private var note: NoteDetail?
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 6) {
                if let note {
                    if let t = note.title ?? title { Text(t).font(.rounded(.headline)).foregroundStyle(Theme.text) }
                    if let d = When.short(note.occurredAt) { Text(d).font(.rounded(.caption2)).foregroundStyle(Theme.muted) }
                    Text(note.text).font(.rounded(.body)).foregroundStyle(Theme.text2)
                } else if let error {
                    ErrorLine(text: error)
                    Button("Try again") { Task { await load() } }.buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
                } else {
                    if let title { Text(title).font(.rounded(.headline)).foregroundStyle(Theme.text) }
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 10)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 2)
        }
        .screen("Note")
        .task { await load() }
    }

    private func load() async {
        error = nil
        do { note = try await model.client.note(id: noteId) } catch { self.error = model.say(error) }
    }
}
