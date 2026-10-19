import AbstractGatewayClient
import AbstractWatchKit
import SwiftUI
import WatchKit

/// Write a note: one large field (dictate, scribble or type), or the microphone. A recording is
/// uploaded and written down by the server; the words fill the field. **Send** sends the words;
/// **Send as recording** keeps the recording with the note. Then the deck's states, one at a time
/// (slide 09): Sending → Received → Organizing → Organized, and the result card: the note's kind,
/// its title and the entities it was linked to. Each note gets its idempotency key when Send is
/// tapped; "Try again" resends the SAME note with the same key, so a retry never saves it twice.
struct NoteScreen: View {
    /// Where a sent note stands (docs/design.md "Sending states").
    enum Stage: Equatable {
        case sending
        /// The server accepted it (the capture answered).
        case received
        /// Filed; the processor is linking it (the note is not `processed` yet).
        case organizing
        /// Understood: its entities and links are in.
        case organized
        /// Still organizing a minute after it was sent: the server carries on.
        case later
        /// The processor gave up on it for now (the queue retries).
        case attention
    }

    enum Phase: Equatable {
        case compose
        case listening
        /// The recording is uploaded and written down.
        case transcribing
        case sent(Stage)
        /// Sent, but the capture's answer is late.
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

    /// How long after Received the watch follows the note's organizing.
    static let organizeWatch: Duration = .seconds(60)

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
    @State private var answer: CaptureAnswer?
    @State private var detail: NoteDetail?
    @State private var follow: Task<Void, Never>?
    @State private var demoPlayed = false
    /// Screenshot scene only: the recording state drawn without opening the microphone.
    @State private var demoListening = false

    var body: some View {
        FitScroll {
            VStack(alignment: .leading, spacing: Space.s) {
                switch phase {
                case .compose: compose
                case .listening: listening
                case .transcribing:
                    StatusHero(symbol: "waveform", color: Theme.indigo, title: "Writing it down", subtitle: "Your recording is on the server.", working: true)
                        .padding(.top, Space.s)
                case .sent(let stage): sent(stage)
                case .late:
                    StatusHero(symbol: "clock", color: Theme.amber, title: "Sent", subtitle: "The answer is taking a while; the note will show in Today.")
                    Spacer(minLength: Space.s)
                    doneButton
                case .needsPerson:
                    StatusHero(symbol: "hand.raised", color: Theme.amber, title: "Waiting for you",
                               subtitle: "This note waits for your OK in AbstractNote on another device.")
                    Spacer(minLength: Space.s)
                    doneButton
                case .failed(let problem): failed(problem)
                }
            }
            .padding(.horizontal, Space.xs)
        }
        .screen("Note")
        .onAppear { location.prepare() }
        .onDisappear { if phase == .listening { listener.cancel(); phase = .compose } }
        .task { await playDemo() }
    }

    // MARK: - Compose

    private var compose: some View {
        Group {
            if let subject = model.subject {
                HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                    Text("About \(subject.title)").font(.rounded(.caption2)).foregroundStyle(Theme.indigo).lineLimit(2)
                    Spacer(minLength: Space.xs)
                    Button { model.subject = nil } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.muted) }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Not about \(subject.title)")
                }
            }
            TextArea(prompt: "What's on your mind?", text: $text)
                .accessibilityLabel("Note")
                .accessibilityHint("Dictate, scribble or type your note")
                .accessibilityIdentifier("note.text")
            if let recording {
                HStack(spacing: Space.xs) {
                    StateLine(symbol: "waveform", text: "Recording \(ListeningView.clock(recording.duration))", color: Theme.indigo)
                    Spacer(minLength: Space.xs)
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
                StateLine(symbol: "location.fill", text: "With your place", color: Theme.muted)
            }
            Spacer(minLength: Space.s)
            HStack(spacing: Space.s) {
                MicButton { startListening() }
                Button("Send") { send(withRecording: false) }
                    .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                    .disabled(empty)
                    .accessibilityIdentifier("note.send")
                    .accessibilityHint("Saves this note to your notebook")
            }
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
            ListeningView(levels: (0..<Listener.bars).map { i in 0.2 + 0.7 * abs(sin(Double(i) * 0.55)) * (i > 17 ? 0.35 : 1) },
                          elapsed: 12, heardSpeech: true, stop: {}, cancel: { phase = .compose })
        } else {
            ListeningView(levels: listener.levels, elapsed: listener.elapsed, heardSpeech: listener.heardSpeech,
                          stop: { listener.send() }, cancel: { listener.cancel(); phase = .compose })
        }
    }

    // MARK: - Sent

    @ViewBuilder private func sent(_ stage: Stage) -> some View {
        switch stage {
        case .sending:
            StatusHero(symbol: "arrow.up", color: Theme.indigo, title: "Sending", subtitle: pending?.text, working: true)
                .padding(.top, Space.s)
        case .received:
            StatusHero(symbol: "checkmark", color: Theme.mint, title: "Received", subtitle: "Your note is on your server.")
            StateLine(symbol: "square.stack.3d.up", text: "Organizing", color: Theme.indigo)
                .frame(maxWidth: .infinity)
        default:
            resultCard(stage)
        }
        if stage != .sending {
            Spacer(minLength: Space.s)
            doneButton
        }
    }

    /// The note as filed: its state in one line, the kind, the title, the entities it is linked to.
    private func resultCard(_ stage: Stage) -> some View {
        VStack(alignment: .leading, spacing: Space.s) {
            switch stage {
            case .organized: StateLine(symbol: "checkmark.seal.fill", text: "Organized", color: Theme.mint)
            case .attention: StateLine(symbol: "exclamationmark.triangle", text: "Needs attention: it will be tried again", color: Theme.amber)
            case .later: StateLine(symbol: "clock", text: "Organizing continues on your server", color: Theme.indigo)
            default: StateLine(symbol: "square.stack.3d.up", text: "Organizing", color: Theme.indigo)
            }
            Card {
                VStack(alignment: .leading, spacing: Space.s) {
                    if let kind = detail?.kind.shown { Pill(kind) }
                    Text(detail?.title.shown ?? answer?.response ?? "Your note")
                        .font(.rounded(.headline)).foregroundStyle(Theme.text)
                        .accessibilityIdentifier("note.answer")
                    if let entities = detail?.entities, !entities.isEmpty {
                        FlowRow(entities) { e in
                            NavigationLink(value: Route.entity(id: e.entityId, name: e.name)) { Chip(readableName(e.name)) }
                                .buttonStyle(.plain)
                                .accessibilityHint("Opens \(readableName(e.name))")
                        }
                    } else if stage == .organizing {
                        Text("Links appear when it is organized.").font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                    }
                }
            }
            if let response = answer?.response, detail?.title.shown != nil {
                Text(response).font(.rounded(.caption2)).foregroundStyle(Theme.muted)
            }
        }
    }

    private var doneButton: some View {
        Button("Done") { reset() }
            .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
            .accessibilityIdentifier("note.done")
            .accessibilityHint("Starts a new note")
    }

    private func failed(_ problem: Problem) -> some View {
        Group {
            Text(pending?.text ?? text).font(.rounded(.footnote)).foregroundStyle(Theme.text2).lineLimit(3)
            Spacer(minLength: Space.s)
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

    /// Starts the capture (or follows the one already started) until the server has it, then
    /// follows the note's organizing.
    private func deliver() async {
        guard let capture = pending else { return }
        phase = .sent(.sending)
        answer = nil; detail = nil
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
            case .done(let a):
                answer = a
                phase = .sent(.received)
                WKInterfaceDevice.current().play(.success)            // the deck's haptic on Received
                if let noteId = a.noteId { followOrganizing(noteId) }
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

    /// Reads the note every 2 s for a minute: Organizing until it is processed (then its kind,
    /// title and entities), Needs attention when the processor gave up for now. A failed read
    /// keeps the last state; after a minute the watch stops asking and says the server carries on.
    private func followOrganizing(_ noteId: String) {
        follow?.cancel()
        let client = model.client
        follow = Task {
            let clock = ContinuousClock()
            let deadline = clock.now + Self.organizeWatch
            try? await Task.sleep(for: .milliseconds(800))
            while !Task.isCancelled {
                if let n = try? await client.note(id: noteId) {
                    detail = n
                    if n.isOrganized { phase = .sent(.organized); WKInterfaceDevice.current().play(.click); return }
                    if n.needsAttention { phase = .sent(.attention); return }
                    phase = .sent(.organizing)
                }
                guard clock.now < deadline else { phase = .sent(.later); return }
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    private func reset() {
        follow?.cancel(); follow = nil
        text = ""; pending = nil; runId = nil; recording = nil; voiceError = nil; answer = nil; detail = nil
        model.subject = nil; phase = .compose
    }

    /// Screenshot scenes (`--mock --screen note-…`).
    private func playDemo() async {
        guard model.isMock, !demoPlayed, let demo = model.demo, demo.hasPrefix("note"), demo != "note-detail" else { return }
        demoPlayed = true
        switch demo {
        case "note": return
        case "note-typed": text = "Met Dana about the LUT budget; she wants the Pareto by Friday."
        case "note-listening": demoListening = true; phase = .listening
        case "note-recording", "note-no-stt": await writeDown(Data("RIFF-sample".utf8), duration: 12)
        default:
            text = "Met Dana about the LUT budget; she wants the Pareto by Friday."
            send(withRecording: false)
        }
    }
}

/// Chips that wrap onto as many lines as they need.
struct FlowRow<Item: Identifiable, Content: View>: View {
    let items: [Item]
    let content: (Item) -> Content
    init(_ items: [Item], @ViewBuilder content: @escaping (Item) -> Content) { self.items = items; self.content = content }
    var body: some View {
        WrapLayout(spacing: Space.xs + 2) { ForEach(items) { content($0) } }
    }
}

/// Left-to-right wrapping layout.
struct WrapLayout: Layout {
    var spacing: CGFloat
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 180
        var x: CGFloat = 0, y: CGFloat = 0, line: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x > 0 && x + s.width > width { x = 0; y += line + spacing; line = 0 }
            x += s.width + spacing; line = max(line, s.height)
        }
        return CGSize(width: width, height: y + line)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, line: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x > bounds.minX && x + s.width > bounds.maxX { x = bounds.minX; y += line + spacing; line = 0 }
            v.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing; line = max(line, s.height)
        }
    }
}
