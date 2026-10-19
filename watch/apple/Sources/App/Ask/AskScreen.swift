import AbstractGatewayClient
import AbstractWatchKit
import SwiftUI
import WatchKit

/// Ask your notes (the deck's slide 10): Quick by default on the watch (the notes that match, at
/// once, no model: `GET /note/search`), Standard one tap away (one answer in sentences with its
/// sources: the `note-recall` flow). The answer is a readable card; the sources are compact rows
/// (title · date) that open the note. "Dig deeper" starts a deep search whose answer shows in Today.
struct AskScreen: View {
    enum Effort: String, CaseIterable { case quick = "Quick", standard = "Standard" }

    enum Phase: Equatable {
        case compose
        case asking
        /// Quick: the notes that match.
        case found([NoteRef])
        /// Standard: the answer and its sources.
        case answer(RecallAnswer)
        case late
        case deeper
        case failed(Problem)
    }

    @EnvironmentObject private var model: AppModel
    @State private var effort: Effort = .quick
    @State private var question = ""
    @State private var asked = ""
    @State private var phase: Phase = .compose
    @State private var startingDeep = false
    @State private var deepError: String?
    @State private var demoPlayed = false

    var body: some View {
        FitScroll {
            VStack(alignment: .leading, spacing: Space.s) {
                switch phase {
                case .compose: compose
                case .asking:
                    StatusHero(symbol: "magnifyingglass", color: Theme.indigo, title: "Looking through your notes", subtitle: asked, working: true)
                        .padding(.top, Space.s)
                case .found(let notes): found(notes)
                case .answer(let a): answer(a)
                case .late: done("Still thinking", "Ask again in a moment.", symbol: "clock", color: Theme.amber)
                case .deeper: done("Digging deeper", "The answer will appear in Today.", symbol: "sparkle.magnifyingglass", color: Theme.indigo)
                case .failed(let p):
                    Text(asked).font(.rounded(.footnote)).foregroundStyle(Theme.text2).lineLimit(3)
                    Spacer(minLength: Space.s)
                    ProblemView(problem: p, signIn: model.signInAgain, retry: { Task { await ask() } })
                    Button("Edit") { phase = .compose }.buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
                }
            }
            .padding(.horizontal, Space.xs)
        }
        .screen("Ask")
        .task { await playDemo() }
    }

    // MARK: - Compose

    private var compose: some View {
        Group {
            EffortPicker(effort: $effort)
            TextArea(prompt: "What do you want to remember?", text: $question, minHeight: 64)
                .accessibilityLabel("Question")
                .accessibilityHint("Dictate, scribble or type a question about your notes")
                .accessibilityIdentifier("ask.text")
            Text(effort == .quick ? "The notes that match, at once." : "One answer with its sources; a few seconds.")
                .font(.rounded(.caption2)).foregroundStyle(Theme.muted)
            Spacer(minLength: Space.s)
            Button("Ask") { asked = question.trimmingCharacters(in: .whitespacesAndNewlines); Task { await ask() } }
                .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                .disabled(question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityIdentifier("ask.send")
        }
    }

    // MARK: - Answers

    private func found(_ notes: [NoteRef]) -> some View {
        Group {
            questionLine
            Card {
                Text(notes.isEmpty ? "Nothing in your notes about that." : notes.count == 1 ? "1 note matches." : "\(notes.count) notes match.")
                    .font(.rounded(.body)).foregroundStyle(Theme.text)
                    .accessibilityIdentifier("ask.answer")
            }
            sources(notes.map { ($0.noteId, $0.title, $0.occurredAt) })
            Spacer(minLength: Space.s)
            Button("Standard answer") { effort = .standard; Task { await ask() } }
                .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                .accessibilityHint("Asks for one answer with its sources")
            Button("Done") { reset() }.buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
        }
    }

    private func answer(_ a: RecallAnswer) -> some View {
        Group {
            questionLine
            Card {
                VStack(alignment: .leading, spacing: Space.s) {
                    Text(Citations.strip(a.response)).font(.rounded(.body)).foregroundStyle(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("ask.answer")
                    if !a.evidence.isEmpty {
                        Text(a.evidence.count == 1 ? "From 1 note" : "From \(a.evidence.count) notes")
                            .font(.rounded(.caption2, weight: .medium)).foregroundStyle(Theme.indigo)
                    }
                }
            }
            sources(a.evidence.map { ($0.noteId, $0.title, $0.occurredAt) })
            if let deepError { ErrorLine(text: deepError) }
            Spacer(minLength: Space.s)
            Button { Task { await digDeeper() } } label: {
                if startingDeep { ProgressView() } else { Text("Dig deeper") }
            }
            .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
            .disabled(startingDeep)
            .accessibilityIdentifier("ask.deeper")
            .accessibilityHint("Starts a longer search; its answer shows in Today")
            Button("Done") { reset() }.buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
        }
    }

    private var questionLine: some View {
        Text(asked).font(.rounded(.footnote, weight: .medium)).foregroundStyle(Theme.text2).lineLimit(2)
    }

    /// Compact source rows: title · date, each opening its note.
    @ViewBuilder private func sources(_ rows: [(id: String, title: String?, at: String?)]) -> some View {
        if !rows.isEmpty {
            SectionTitle(text: "Sources")
            ForEach(rows.prefix(8), id: \.id) { r in
                NavigationLink(value: Route.note(id: r.id, title: r.title.shown)) {
                    HStack(spacing: Space.s) {
                        Image(systemName: "doc.text").font(.caption).foregroundStyle(Theme.indigo)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(r.title.shown ?? "A note").font(.rounded(.footnote, weight: .medium)).foregroundStyle(Theme.text).lineLimit(2)
                            if let d = When.short(r.at) { Text(d).font(.rounded(.caption2)).foregroundStyle(Theme.muted) }
                        }
                        Spacer(minLength: 0)
                    }
                    .accessibilityElement(children: .combine)
                    .padding(.vertical, Space.xs + 2).padding(.horizontal, Space.s + 2)
                    .background(Theme.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityHint("Opens this note")
            }
        }
    }

    private func done(_ title: String, _ line: String, symbol: String, color: Color) -> some View {
        Group {
            questionLine
            StatusHero(symbol: symbol, color: color, title: title, subtitle: line)
            Spacer(minLength: Space.s)
            Button("Done") { reset() }.buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
        }
    }

    // MARK: - Actions

    private func ask() async {
        guard !asked.isEmpty else { return }
        phase = .asking
        RuntimeKeeper.shared.hold("ask")
        defer { RuntimeKeeper.shared.release("ask") }
        let client = model.client
        do {
            if effort == .quick {
                phase = .found(try await client.quickSearch(asked, limit: 8).notes)
                return
            }
            let id = try await client.startRecall(question: asked, effort: .standard, sessionId: model.sessionId)
            let p = try await client.follow({ try await client.recallProgress(runId: id) }, every: .seconds(1), timeout: .seconds(90))
            switch p {
            case .done(let a): phase = .answer(a); WKInterfaceDevice.current().play(.success)
            case .working: phase = .late
            case .needsPerson: phase = .failed(.said("No answer this time. Try again."))
            // The flow's own sentence when it gave one (without the runtime's prefix).
            case .failed(let why): phase = .failed(.said(why ?? "No answer this time. Try again."))
            }
        } catch {
            phase = .failed(await model.problem(error))
        }
    }

    private func digDeeper() async {
        startingDeep = true; deepError = nil
        defer { startingDeep = false }
        do {
            _ = try await model.client.startRecall(question: asked, effort: .deep, sessionId: model.sessionId)
            phase = .deeper
        } catch {
            deepError = await model.problem(error).sentence
        }
    }

    private func reset() { question = ""; asked = ""; deepError = nil; effort = .quick; phase = .compose }

    /// Screenshot scenes (`--mock --screen ask-…`).
    private func playDemo() async {
        guard model.isMock, !demoPlayed, let demo = model.demo, demo.hasPrefix("ask"), demo != "ask" else { return }
        demoPlayed = true
        question = "What did I decide about the LUT budget?"
        asked = question
        effort = demo == "ask-quick" ? .quick : .standard
        await ask()
        if demo == "ask-deeper" { await digDeeper() }
    }
}

/// Quick or Standard, as two halves of one capsule. The chosen one is filled AND bold (and says
/// so to VoiceOver): never colour alone.
struct EffortPicker: View {
    @Binding var effort: AskScreen.Effort
    var body: some View {
        HStack(spacing: 2) {
            ForEach(AskScreen.Effort.allCases, id: \.self) { e in
                let on = e == effort
                Button { effort = e } label: {
                    Text(e.rawValue)
                        .font(.rounded(.footnote, weight: on ? .bold : .regular))
                        .foregroundStyle(on ? Theme.text : Theme.text2)
                        .frame(maxWidth: .infinity, minHeight: 32)
                        .background(on ? Theme.indigoButton : Color.clear, in: Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(e.rawValue)
                .accessibilityAddTraits(on ? .isSelected : [])
                .accessibilityIdentifier("ask.effort.\(e.rawValue.lowercased())")
            }
        }
        .padding(2)
        .background(Theme.card, in: Capsule())
    }
}
