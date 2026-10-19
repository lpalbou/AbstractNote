import AbstractGatewayClient
import SwiftUI
import WatchKit

/// Ask your notes: a question answered at standard effort, with the notes it rests on. "Dig
/// deeper" starts a deep search that runs on its own; its answer shows in Today when ready.
struct AskScreen: View {
    enum Phase: Equatable {
        case compose
        case asking
        case answer(RecallAnswer)
        case late
        case deeper
        case failed(String)
    }

    @EnvironmentObject private var model: AppModel
    @State private var question = ""
    @State private var asked = ""
    @State private var phase: Phase = .compose
    @State private var startingDeep = false
    @State private var deepError: String?
    @State private var demoPlayed = false

    var body: some View {
        FitScroll {
            VStack(alignment: .leading, spacing: 8) {
                switch phase {
                case .compose: compose
                case .asking: asking
                case .answer(let a): answer(a)
                case .late: done("Still thinking. Ask again in a moment.", symbol: "clock", color: Theme.amber)
                case .deeper: done("Digging deeper. The answer will appear in Today.", symbol: "sparkle.magnifyingglass", color: Theme.cyan)
                case .failed(let m):
                    Text(asked).font(.rounded(.footnote)).foregroundStyle(Theme.text2).lineLimit(3)
                    ErrorLine(text: m)
                    Spacer(minLength: 6)
                    Button("Try again") { Task { await ask() } }.buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                    Button("Edit") { phase = .compose }.buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
                }
            }
            .padding(.horizontal, 2)
        }
        .screen("Ask")
        .task { await playDemo() }
    }

    private var compose: some View {
        Group {
            TextField("What do you want to remember?", text: $question, axis: .vertical)
                .lineLimit(1...5)
                .font(.rounded(.body))
                .accessibilityLabel("Question")
                .accessibilityHint("Dictate, scribble or type a question about your notes")
                .accessibilityIdentifier("ask.text")
            Spacer(minLength: 6)
            Button("Ask") { asked = question.trimmingCharacters(in: .whitespacesAndNewlines); Task { await ask() } }
                .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                .disabled(question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityIdentifier("ask.send")
        }
    }

    private var asking: some View {
        VStack(spacing: 10) {
            Text(asked).font(.rounded(.footnote)).foregroundStyle(Theme.text2).lineLimit(4)
                .frame(maxWidth: .infinity, alignment: .leading)
            Spacer(minLength: 4)
            ProgressView()
            LoadingText(text: "Looking through your notes").font(.rounded(.footnote)).foregroundStyle(Theme.muted)
                .multilineTextAlignment(.center)
        }
        .accessibilityElement(children: .combine)
    }

    private func answer(_ a: RecallAnswer) -> some View {
        Group {
            Text(asked).font(.rounded(.caption2)).foregroundStyle(Theme.muted).lineLimit(2)
            Text(a.response).font(.rounded(.body)).foregroundStyle(Theme.text)
                .accessibilityIdentifier("ask.answer")
            if !a.evidence.isEmpty {
                SectionTitle(text: "From your notes")
                ForEach(a.evidence) { e in
                    NavigationLink(value: Route.note(id: e.noteId, title: e.title)) {
                        Card {
                            VStack(alignment: .leading, spacing: 1) {
                                Text(e.title ?? "A note").font(.rounded(.footnote, weight: .semibold)).foregroundStyle(Theme.text).lineLimit(2)
                                if let d = When.short(e.occurredAt) { Text(d).font(.rounded(.caption2)).foregroundStyle(Theme.muted) }
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens this note")
                }
            }
            if let deepError { ErrorLine(text: deepError) }
            Spacer(minLength: 6)
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

    private func done(_ sentence: String, symbol: String, color: Color) -> some View {
        Group {
            Text(asked).font(.rounded(.caption2)).foregroundStyle(Theme.muted).lineLimit(2)
            Label { Text(sentence).font(.rounded(.body)).foregroundStyle(Theme.text) } icon: { Image(systemName: symbol).foregroundStyle(color) }
            Spacer(minLength: 6)
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
            let id = try await client.startRecall(question: asked, effort: .standard, sessionId: model.sessionId)
            let p = try await client.follow({ try await client.recallProgress(runId: id) }, every: .seconds(1), timeout: .seconds(90))
            switch p {
            case .done(let a): phase = .answer(a); WKInterfaceDevice.current().play(.success)
            case .working: phase = .late
            case .needsPerson: phase = .failed("No answer this time. Try again.")
            // The flow's own sentence when it gave one (without the runtime's prefix).
            case .failed(let why): phase = .failed(why ?? "No answer this time. Try again.")
            }
        } catch {
            phase = .failed(model.say(error))
        }
    }

    private func digDeeper() async {
        startingDeep = true; deepError = nil
        defer { startingDeep = false }
        do {
            _ = try await model.client.startRecall(question: asked, effort: .deep, sessionId: model.sessionId)
            phase = .deeper
        } catch {
            deepError = model.say(error)
        }
    }

    private func reset() { question = ""; asked = ""; deepError = nil; phase = .compose }

    /// Screenshot scenes (`--mock --screen ask-…`).
    private func playDemo() async {
        guard model.isMock, !demoPlayed, let demo = model.demo, demo.hasPrefix("ask"), demo != "ask" else { return }
        demoPlayed = true
        question = "What did I decide about the LUT budget?"
        asked = question
        await ask()
        if demo == "ask-deeper" { await digDeeper() }
    }
}
