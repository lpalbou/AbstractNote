import AbstractGatewayClient
import AbstractWatchKit
import SwiftUI
import WatchKit

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
                        Label("The note it came from", systemImage: "doc.text").font(.rounded(.footnote)).foregroundStyle(Theme.indigo)
                    }
                    .buttonStyle(.plain)
                }
                if let error { ErrorLine(text: error) }
                Spacer(minLength: 6)
                Button { Task { await close() } } label: { if working { ProgressView() } else { Text("Close") } }
                    .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton)).disabled(working)
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
            self.error = await model.problem(error).sentence; return
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
                // "Why": the notes behind the cue, when the notebook named what it is about.
                if let subject = cue.subject, !cue.evidenceNoteIds.isEmpty {
                    Text("About \(subject.title)").font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                    NavigationLink(value: Route.why(title: subject.title, noteIds: cue.evidenceNoteIds)) {
                        Label("Why", systemImage: "questionmark.circle").font(.rounded(.footnote)).foregroundStyle(Theme.indigo)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Shows the notes behind this")
                    .accessibilityIdentifier("cue.why")
                }
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

/// One note: its kind, title, words, when it was written, the entities it is linked to, and for
/// an insight the notes behind it.
struct NoteDetailView: View {
    let noteId: String
    let title: String?
    @EnvironmentObject private var model: AppModel
    @State private var note: NoteDetail?
    @State private var problem: Problem?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.s) {
                if let note {
                    HStack(spacing: Space.s) {
                        if let kind = note.kind.shown { Pill(kind) }
                        if let d = When.short(note.occurredAt) { Text(d).font(.rounded(.caption2)).foregroundStyle(Theme.muted) }
                    }
                    if let t = note.title.shown ?? title.shown { Text(t).font(.rounded(.headline)).foregroundStyle(Theme.text) }
                    Card {
                        Text(note.text).font(.rounded(.body)).foregroundStyle(Theme.text2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if !note.entities.isEmpty {
                        SectionTitle(text: "Linked to")
                        FlowRow(note.entities) { e in
                            NavigationLink(value: Route.entity(id: e.entityId, name: e.name)) { Chip(readableName(e.name)) }
                                .buttonStyle(.plain)
                        }
                    }
                    if let ids = note.insight?.evidenceNoteIds, !ids.isEmpty {
                        NavigationLink(value: Route.why(title: note.title.shown ?? "This", noteIds: ids)) {
                            StateLine(symbol: "questionmark.circle", text: "Why", color: Theme.indigo)
                        }
                        .buttonStyle(.plain).padding(.top, Space.xs)
                        .accessibilityHint("Shows the notes behind this")
                    }
                } else if let problem {
                    ProblemView(problem: problem, signIn: model.signInAgain, retry: { Task { await load() } })
                } else {
                    if let t = title.shown { Text(t).font(.rounded(.headline)).foregroundStyle(Theme.text) }
                    ProgressView().frame(maxWidth: .infinity).padding(.top, Space.s)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Space.xs)
        }
        .screen("Note")
        .task { if note == nil { await load() } }
    }

    private func load() async {
        problem = nil
        do { note = try await model.client.note(id: noteId) } catch { problem = await model.problem(error) }
    }
}

/// An entity: its name, kind, theme, summary, when it was first and last noted, how what you know
/// about it evolved (`/timeline`), and its notes.
struct EntityView: View {
    let entityId: String
    let name: String
    @EnvironmentObject private var model: AppModel
    @State private var card: EntityCard?
    @State private var milestones: [EntityTimeline.Milestone] = []
    @State private var problem: Problem?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.s) {
                Text(readableName(card?.name ?? name)).font(.rounded(.title3, weight: .semibold)).foregroundStyle(Theme.text)
                    .accessibilityAddTraits(.isHeader)
                if let card {
                    HStack(spacing: Space.s) {
                        if let kind = card.kind.shown { Pill(kind) }
                        if let c = card.community {
                            Label(c.label.shown ?? "A theme not named yet", systemImage: "circle.hexagongrid")
                                .font(.rounded(.caption2)).foregroundStyle(Theme.text2).lineLimit(1)
                        }
                    }
                    if let summary = card.summary.shown {
                        Text(summary).font(.rounded(.body)).foregroundStyle(Theme.text2).fixedSize(horizontal: false, vertical: true)
                    }
                    if let seen = seenLine(card) { Text(seen).font(.rounded(.caption2)).foregroundStyle(Theme.muted) }
                    if !milestones.isEmpty {
                        SectionTitle(text: "How it evolved").padding(.top, Space.s)
                        ForEach(milestones) { m in milestone(m) }
                    }
                    if !card.notes.isEmpty {
                        SectionTitle(text: "Notes").padding(.top, Space.s)
                        ForEach(card.notes.prefix(8)) { n in
                            NavigationLink(value: Route.note(id: n.noteId, title: n.title.shown)) {
                                HStack(spacing: Space.s) {
                                    Image(systemName: "doc.text").font(.caption).foregroundStyle(Theme.indigo)
                                    VStack(alignment: .leading, spacing: 1) {
                                        Text(n.title.shown ?? "A note").font(.rounded(.footnote, weight: .medium)).foregroundStyle(Theme.text).lineLimit(2)
                                        if let d = When.short(n.occurredAt) { Text(d).font(.rounded(.caption2)).foregroundStyle(Theme.muted) }
                                    }
                                    Spacer(minLength: 0)
                                }
                                .accessibilityElement(children: .combine)
                                .padding(.vertical, Space.xs + 2).padding(.horizontal, Space.s + 2)
                                .background(Theme.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                } else if let problem {
                    ProblemView(problem: problem, signIn: model.signInAgain, retry: { Task { await load() } })
                } else {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, Space.s)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Space.xs)
        }
        .screen("About")
        .task { if card == nil { await load() } }
    }

    /// "First noted 2 Sep · last 17 Oct"
    private func seenLine(_ c: EntityCard) -> String? {
        let first = When.short(c.firstSeen), last = When.short(c.lastSeen)
        switch (first, last) {
        case let (f?, l?) where f != l: return "First noted \(f) · last \(l)"
        case let (f?, _): return "First noted \(f)"
        default: return nil
        }
    }

    /// One step: the date, its kind as a word (never colour alone), its words.
    private func milestone(_ m: EntityTimeline.Milestone) -> some View {
        let (word, symbol): (String, String) = switch m.kind {
        case .noted: ("Noted", "doc.text")
        case .learned: ("Learned", "plus.circle")
        case .changed: ("No longer held", "arrow.triangle.2.circlepath")
        }
        let content = HStack(alignment: .top, spacing: Space.s) {
            Image(systemName: symbol).font(.caption).foregroundStyle(m.kind == .changed ? Theme.amber : Theme.indigo).frame(width: 16).padding(.top, 2)
            VStack(alignment: .leading, spacing: 1) {
                Text([word, When.short(m.at)].compactMap { $0 }.joined(separator: " · "))
                    .font(.rounded(.caption2, weight: .medium)).foregroundStyle(Theme.muted)
                Text(m.text).font(.rounded(.footnote)).foregroundStyle(m.kind == .changed ? Theme.text2 : Theme.text)
                    .strikethrough(m.kind == .changed, color: Theme.muted)
                    .lineLimit(3)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        return Group {
            if let id = m.noteId {
                NavigationLink(value: Route.note(id: id, title: nil)) { content }.buttonStyle(.plain)
            } else { content }
        }
        .padding(.vertical, Space.xs)
    }

    private func load() async {
        problem = nil
        let client = model.client
        do {
            async let c = client.entity(id: entityId)
            async let t = client.entityTimeline(id: entityId)
            card = try await c
            milestones = (try await t).milestones()
        } catch {
            problem = await model.problem(error)
        }
    }
}

/// A theme: what it gathers (its summary) and its main entities, each opening its page.
struct ThemeView: View {
    let community: Community

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.s) {
                Text(community.label.shown ?? "Not named yet").font(.rounded(.title3, weight: .semibold)).foregroundStyle(Theme.text)
                HStack(spacing: Space.s) {
                    if let kind = community.kind.shown { Pill(kind) }
                    Text(community.size == 1 ? "1 entry" : "\(community.size) entries").font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                }
                if let summary = community.summary.shown {
                    Text(summary).font(.rounded(.body)).foregroundStyle(Theme.text2).fixedSize(horizontal: false, vertical: true)
                } else if community.label.shown == nil {
                    Text("The notebook names it at its next reflection.").font(.rounded(.footnote)).foregroundStyle(Theme.muted)
                }
                if !community.topEntities.isEmpty {
                    SectionTitle(text: "Mostly about").padding(.top, Space.s)
                    ForEach(community.topEntities) { e in
                        NavigationLink(value: Route.entity(id: e.entityId, name: e.name)) {
                            HStack(spacing: Space.s) {
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(readableName(e.name)).font(.rounded(.footnote, weight: .medium)).foregroundStyle(Theme.text).lineLimit(1)
                                    Text([e.kind, e.noteCount.map { $0 == 1 ? "1 note" : "\($0) notes" }].compactMap { $0 }.joined(separator: " · "))
                                        .font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                                }
                                Spacer(minLength: 0)
                                Image(systemName: "chevron.right").font(.caption2).foregroundStyle(Theme.muted)
                            }
                            .padding(.horizontal, 12).padding(.vertical, Space.s)
                            .background(Theme.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Space.xs)
        }
        .screen("Theme")
    }
}

/// The notes behind a cue or an insight, each opening on its own page.
struct WhyView: View {
    let title: String
    let noteIds: [String]
    @EnvironmentObject private var model: AppModel
    @State private var notes: [NoteDetail]?
    @State private var problem: Problem?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 6) {
                Text("Behind this, from your notes").font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                if let notes {
                    ForEach(notes) { n in
                        NavigationLink(value: Route.note(id: n.noteId, title: n.title.shown)) {
                            Card {
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(n.title.shown ?? n.text).font(.rounded(.footnote, weight: .semibold)).foregroundStyle(Theme.text).lineLimit(2)
                                    if let d = When.short(n.occurredAt) { Text(d).font(.rounded(.caption2)).foregroundStyle(Theme.muted) }
                                }
                            }
                        }
                        .buttonStyle(.plain)
                        .accessibilityHint("Opens this note")
                    }
                } else if let problem {
                    ProblemView(problem: problem, signIn: model.signInAgain, retry: { Task { await load() } })
                } else {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 10)
                }
            }
            .padding(.horizontal, 2)
        }
        .screen("Why")
        .task { if notes == nil { await load() } }
    }

    /// The notes, read together (a cue rests on a few).
    private func load() async {
        problem = nil
        let client = model.client
        do {
            notes = try await withThrowingTaskGroup(of: (Int, NoteDetail).self) { group in
                for (i, id) in noteIds.prefix(8).enumerated() { group.addTask { (i, try await client.note(id: id)) } }
                var out: [(Int, NoteDetail)] = []
                for try await r in group { out.append(r) }
                return out.sorted { $0.0 < $1.0 }.map(\.1)
            }
        } catch {
            problem = await model.problem(error)
        }
    }
}
