import AbstractGatewayClient
import AbstractWatchKit
import SwiftUI
import WatchKit

/// Write a note: dictate, scribble or type it in the field, or tap the microphone and speak. A
/// recording is uploaded and written down by the server's speech to text; the words fill the field
/// (editable). **Send** sends the words; **Send as recording** sends them with the recording kept
/// as the note's attachment (the original audio stays with the note). The answer says how it was
/// filed. Each note gets its idempotency key when Send is tapped; "Try again" resends the SAME
/// note with the same key, so a radio retry never saves it twice.
struct NoteScreen: View {
    enum Phase: Equatable {
        case compose
        case listening
        /// The recording is uploaded and written down.
        case transcribing
        case sending
        case saved(String)
        /// Sent, but its answer is late.
        case late
        /// Stopped on an approval the watch cannot give.
        case needsPerson
        case failed(Problem)
    }

    /// A recording written down: the uploaded audio (kept for "Send as recording") and its length.
    struct Recording: Equatable {
        var audio: UploadedAttachment
        var duration: TimeInterval
    }

    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var location: Location
    @StateObject private var listener = Listener()
    @State private var text = ""
    @State private var phase: Phase = .compose
    @State private var recording: Recording?
    @State private var voiceError: Problem?
    /// The note being sent: kept for "Try again" (same key, same time, same place).
    @State private var pending: Capture?
    @State private var runId: String?
    @State private var demoPlayed = false
    /// Screenshot scene only: the recording state drawn without opening the microphone.
    @State private var demoListening = false

    var body: some View {
        FitScroll {
            VStack(alignment: .leading, spacing: 8) {
                switch phase {
                case .compose: compose
                case .listening: listening
                case .transcribing: waiting("Writing it down")
                case .sending: sending
                case .saved(let answer): result(answer, symbol: "checkmark.circle.fill", color: Theme.green)
                case .late: result("Sent. Its answer is taking a while; the note will show in Today.", symbol: "clock", color: Theme.amber)
                case .needsPerson: result("This note waits for your OK in AbstractNote on another device.", symbol: "hand.raised", color: Theme.amber)
                case .failed(let problem): failed(problem)
                }
            }
            .padding(.horizontal, 2)
        }
        .screen("Note")
        .onAppear { location.prepare() }
        .onDisappear { if phase == .listening { listener.cancel(); phase = .compose } }
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
            HStack(alignment: .center, spacing: 6) {
                TextField("What's on your mind?", text: $text, axis: .vertical)
                    .lineLimit(1...6)
                    .font(.rounded(.body))
                    .accessibilityLabel("Note")
                    .accessibilityHint("Dictate, scribble or type your note")
                    .accessibilityIdentifier("note.text")
                MicButton { startListening() }
            }
            if let recording {
                HStack(spacing: 4) {
                    Label("Recording \(ListeningView.clock(recording.duration))", systemImage: "waveform")
                        .font(.rounded(.caption2)).foregroundStyle(Theme.cyan)
                    Spacer(minLength: 2)
                    Button { self.recording = nil } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.muted) }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Drop the recording")
                }
                .accessibilityIdentifier("note.recording")
            }
            if let voiceError {
                ErrorLine(text: voiceError.sentence)
                if voiceError.action == .signIn {
                    Button("Sign in", action: model.signInAgain).buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                }
            }
            if location.enabled && location.allowed {
                Label("With your place", systemImage: "location.fill")
                    .font(.rounded(.caption2)).foregroundStyle(Theme.muted)
            }
            Spacer(minLength: 6)
            Button("Send") { send(withRecording: false) }
                .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                .disabled(empty)
                .accessibilityIdentifier("note.send")
                .accessibilityHint("Saves this note to your notebook")
            if recording != nil {
                Button("Send as recording") { send(withRecording: true) }
                    .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
                    .accessibilityIdentifier("note.sendRecording")
                    .accessibilityHint("Saves the words and keeps the recording with the note")
            }
        }
    }

    private var empty: Bool { text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    @ViewBuilder private var listening: some View {
        if demoListening {
            ListeningView(levels: (0..<Listener.bars).map { i in 0.25 + 0.6 * abs(sin(Double(i) * 0.7)) * (i > 16 ? 0.4 : 1) },
                          elapsed: 7, heardSpeech: true, stop: {}, cancel: { phase = .compose })
        } else {
            ListeningView(levels: listener.levels, elapsed: listener.elapsed, heardSpeech: listener.heardSpeech,
                          stop: { listener.send() }, cancel: { listener.cancel(); phase = .compose })
        }
    }

    private func waiting(_ words: String) -> some View {
        VStack(spacing: 10) {
            Spacer(minLength: 4)
            ProgressView()
            LoadingText(text: words).font(.rounded(.footnote)).foregroundStyle(Theme.muted)
        }
        .frame(maxWidth: .infinity)
    }

    private var sending: some View {
        VStack(spacing: 10) {
            Text(pending?.text ?? text).font(.rounded(.footnote)).foregroundStyle(Theme.text2).lineLimit(4)
                .frame(maxWidth: .infinity, alignment: .leading)
            if pending?.attachments.isEmpty == false {
                Label("With its recording", systemImage: "waveform").font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
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

    private func failed(_ problem: Problem) -> some View {
        Group {
            Text(pending?.text ?? text).font(.rounded(.footnote)).foregroundStyle(Theme.text2).lineLimit(3)
            Spacer(minLength: 6)
            ProblemView(problem: problem, signIn: model.signInAgain, retry: { Task { await deliver() } })
                .accessibilityIdentifier("note.error")
            Button("Edit") { phase = .compose; pending = nil; runId = nil }
                .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
        }
    }

    // MARK: - Voice

    private func startListening() {
        voiceError = nil
        phase = .listening
        listener.start { outcome in
            Task { @MainActor in await heard(outcome) }
        }
    }

    /// The recording ended: upload it, write it down, put the words in the field.
    private func heard(_ outcome: Listener.Outcome) async {
        switch outcome {
        case .nothingHeard:
            phase = .compose; voiceError = .said("Nothing was heard. Tap the microphone and speak.")
        case .failed(let sentence):
            phase = .compose; voiceError = .said(sentence)
        case .speech(let wav, let duration):
            await writeDown(wav, duration: duration)
        }
    }

    private func writeDown(_ wav: Data, duration: TimeInterval) async {
        phase = .transcribing
        RuntimeKeeper.shared.hold("transcribe")
        defer { RuntimeKeeper.shared.release("transcribe") }
        do {
            let audio = try await model.client.upload(wav: wav, sessionId: model.sessionId)
            // Uploaded: the recording is kept whatever the transcription does, so "Send as
            // recording" stays possible on a server without speech to text (gate 03 m3).
            recording = Recording(audio: audio, duration: duration)
            let words = try await model.client.transcribe(audio)
            text = text.isEmpty ? words : text + " " + words
            if words.isEmpty { voiceError = .said("No words were found in the recording; it is kept.") }
            WKInterfaceDevice.current().play(.success)
        } catch {
            voiceError = await model.problem(error)
            WKInterfaceDevice.current().play(.failure)
        }
        phase = .compose
    }

    // MARK: - Sending

    private func send(withRecording: Bool) {
        var body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // A recording with no words (nothing typed, no transcription) still goes, named as what it is.
        if body.isEmpty, withRecording, let r = recording { body = "Voice note (\(ListeningView.clock(r.duration)))" }
        guard !body.isEmpty else { return }
        pending = Capture(text: body, occurredAt: .now, location: location.current, idempotencyKey: UUID().uuidString.lowercased(),
                          sessionId: model.sessionId, about: model.subject,
                          attachments: withRecording ? recording.map { [$0.audio.reference] } ?? [] : [])
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
            if let runId { id = runId } else {
                await model.processorReady()
                id = try await client.startCapture(capture); runId = id
            }
            let p = try await client.follow({ try await client.captureProgress(runId: id) }, every: .seconds(1), timeout: .seconds(60))
            switch p {
            case .done(let answer): phase = .saved(answer.response); WKInterfaceDevice.current().play(.success)
            case .working: phase = .late
            case .needsPerson: phase = .needsPerson
            case .failed(let why):
                // The run failed: a new try starts a new run with the same key. The flow's own
                // sentence is shown when it gave one (without the runtime's prefix).
                runId = nil
                phase = .failed(.said(why ?? "Your note could not be saved. Try again."))
                WKInterfaceDevice.current().play(.failure)
            }
        } catch {
            phase = .failed(await model.problem(error))
            WKInterfaceDevice.current().play(.failure)
        }
    }

    private func reset() {
        text = ""; pending = nil; runId = nil; recording = nil; voiceError = nil; model.subject = nil; phase = .compose
    }

    /// Screenshot scenes (`--mock --screen note-…`).
    private func playDemo() async {
        guard model.isMock, !demoPlayed, let demo = model.demo, demo.hasPrefix("note"), demo != "note-detail" else { return }
        demoPlayed = true
        switch demo {
        case "note": return
        case "note-listening": demoListening = true; phase = .listening
        case "note-recording", "note-no-stt": await writeDown(Data("RIFF-sample".utf8), duration: 12)
        default:
            text = "Met Dana about the LUT budget; she wants the Pareto by Friday."
            send(withRecording: false)
        }
    }
}
