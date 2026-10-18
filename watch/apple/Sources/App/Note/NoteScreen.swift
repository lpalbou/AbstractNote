import AbstractGatewayClient
import SwiftUI
import WatchKit

/// Write a note: dictate, scribble or type it, then Send. The answer says how it was filed.
/// Each note gets its idempotency key when Send is tapped; "Try again" resends the SAME note with
/// the same key, so a radio retry never saves it twice.
struct NoteScreen: View {
    enum Phase: Equatable {
        case compose
        case sending
        case saved(String)
        /// Sent, but its answer is late.
        case late
        /// Stopped on an approval the watch cannot give.
        case needsPerson
        case failed(String)
    }

    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var location: Location
    @State private var text = ""
    @State private var phase: Phase = .compose
    /// The note being sent: kept for "Try again" (same key, same time, same place).
    @State private var pending: Capture?
    @State private var runId: String?
    @State private var demoPlayed = false

    var body: some View {
        FitScroll {
            VStack(alignment: .leading, spacing: 8) {
                switch phase {
                case .compose: compose
                case .sending: sending
                case .saved(let answer): result(answer, symbol: "checkmark.circle.fill", color: Theme.green)
                case .late: result("Sent. Its answer is taking a while; the note will show in Today.", symbol: "clock", color: Theme.amber)
                case .needsPerson: result("This note waits for your OK in AbstractNote on another device.", symbol: "hand.raised", color: Theme.amber)
                case .failed(let message): failed(message)
                }
            }
            .padding(.horizontal, 2)
        }
        .screen("Note")
        .onAppear { location.prepare() }
        .task { await playDemo() }
    }

    // MARK: - Phases

    private var compose: some View {
        Group {
            if let subject = model.subject {
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Text("About: \(subject.title)").font(.rounded(.caption2)).foregroundStyle(Theme.cyan).lineLimit(2)
                    Spacer(minLength: 2)
                    Button { model.subject = nil } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.muted) }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Not about \(subject.title)")
                }
            }
            TextField("What's on your mind?", text: $text, axis: .vertical)
                .lineLimit(1...6)
                .font(.rounded(.body))
                .accessibilityLabel("Note")
                .accessibilityHint("Dictate, scribble or type your note")
                .accessibilityIdentifier("note.text")
            if location.enabled && location.allowed {
                Label("With your place", systemImage: "location.fill")
                    .font(.rounded(.caption2)).foregroundStyle(Theme.muted)
            }
            Spacer(minLength: 6)
            Button("Send") { send() }
                .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityIdentifier("note.send")
                .accessibilityHint("Saves this note to your notebook")
        }
    }

    private var sending: some View {
        VStack(spacing: 10) {
            Text(pending?.text ?? text).font(.rounded(.footnote)).foregroundStyle(Theme.text2).lineLimit(4)
                .frame(maxWidth: .infinity, alignment: .leading)
            Spacer(minLength: 4)
            ProgressView()
            LoadingText(text: "Saving").font(.rounded(.footnote)).foregroundStyle(Theme.muted)
        }
        .accessibilityElement(children: .combine)
    }

    private func result(_ sentence: String, symbol: String, color: Color) -> some View {
        Group {
            Label { Text(sentence).font(.rounded(.body)).foregroundStyle(Theme.text) } icon: {
                Image(systemName: symbol).foregroundStyle(color)
            }
            .accessibilityIdentifier("note.answer")
            Spacer(minLength: 6)
            Button("Done") { reset() }
                .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
                .accessibilityIdentifier("note.done")
                .accessibilityHint("Starts a new note")
        }
    }

    private func failed(_ message: String) -> some View {
        Group {
            Text(pending?.text ?? text).font(.rounded(.footnote)).foregroundStyle(Theme.text2).lineLimit(3)
            ErrorLine(text: message).accessibilityIdentifier("note.error")
            Spacer(minLength: 6)
            Button("Try again") { Task { await deliver() } }
                .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                .accessibilityHint("Sends the same note again")
            Button("Edit") { phase = .compose; pending = nil; runId = nil }
                .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
        }
    }

    // MARK: - Actions

    private func send() {
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return }
        pending = Capture(text: body, occurredAt: .now, location: location.current, idempotencyKey: UUID().uuidString.lowercased(),
                          sessionId: model.sessionId, about: model.subject)
        runId = nil
        Task { await deliver() }
    }

    /// Starts the capture (or follows the one already started) until its answer is in.
    private func deliver() async {
        guard let capture = pending else { return }
        phase = .sending
        RuntimeKeeper.shared.hold("note")
        defer { RuntimeKeeper.shared.release("note") }
        let client = model.client
        do {
            let id: String
            if let runId { id = runId } else { id = try await client.startCapture(capture); runId = id }
            let p = try await client.follow({ try await client.captureProgress(runId: id) }, every: .seconds(1), timeout: .seconds(60))
            switch p {
            case .done(let answer): phase = .saved(answer.response); WKInterfaceDevice.current().play(.success)
            case .working: phase = .late
            case .needsPerson: phase = .needsPerson
            case .failed:
                // The run failed: a new try starts a new run with the same key.
                runId = nil
                phase = .failed("Your note could not be saved. Try again.")
                WKInterfaceDevice.current().play(.failure)
            }
        } catch {
            phase = .failed(model.say(error))
            WKInterfaceDevice.current().play(.failure)
        }
    }

    private func reset() {
        text = ""; pending = nil; runId = nil; model.subject = nil; phase = .compose
    }

    /// Screenshot scenes (`--mock --screen note-…`): a sample note typed and sent.
    private func playDemo() async {
        guard model.isMock, !demoPlayed, let demo = model.demo, demo.hasPrefix("note"), demo != "note-detail" else { return }
        demoPlayed = true
        if demo == "note" { return }
        text = "Met Dana about the LUT budget; she wants the Pareto by Friday."
        send()
    }
}
