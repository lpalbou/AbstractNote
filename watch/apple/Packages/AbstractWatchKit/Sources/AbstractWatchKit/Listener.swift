#if os(watchOS)
@preconcurrency import AVFoundation
import SwiftUI
import WatchKit

/// The microphone, tap to talk (adapted from the AbstractAssistant watch's Listener.swift): records
/// 16 kHz mono 16-bit WAV, tells speech from the room's noise floor, and ends by itself
/// `silenceToSend` seconds after the person stops speaking, after `waitForSpeech` seconds when
/// nothing was ever heard, or at `maxDuration`. Stop ends it at once. The assistant's spectrum
/// analysis for its orb is left out; `levels` (recent loudness, 0…1) draws the waveform.
@MainActor
public final class Listener: ObservableObject {
    public enum Outcome: Sendable { case speech(Data, duration: TimeInterval), nothingHeard, failed(String) }

    /// Seconds of silence that end a recording (the operator's rule: 3 s, or a tap on Stop).
    public static let silenceToSend: TimeInterval = 3
    /// Time to start speaking after the tap before listening gives up.
    public static let waitForSpeech: TimeInterval = 5
    public static let maxDuration: TimeInterval = 60
    /// How many recent loudness values the waveform shows.
    public static let bars = 24

    @Published public private(set) var isListening = false
    @Published public private(set) var heardSpeech = false
    @Published public private(set) var elapsed: TimeInterval = 0
    @Published public private(set) var levels: [Double] = Array(repeating: 0, count: Listener.bars)

    private var tap: MicTap?
    private var loop: Task<Void, Never>?
    private var finish: ((Outcome) -> Void)?
    private var floorDB: Double = -50
    private var startedAt = Date()
    /// Bumped by every start/stop: a setup that finishes after Cancel or Back never records.
    private var generation = 0
    private var lastSpeech = Date()
    private let url = FileManager.default.temporaryDirectory.appending(path: "note.wav")

    public init() {}

    /// Starts recording; `done` is called once, with the audio, "nothing heard" or the failure.
    public func start(done: @escaping (Outcome) -> Void) {
        stop(deliver: false)
        finish = done
        let mine = generation
        Task {
            let allowed = await AVAudioApplication.requestRecordPermission()
            guard mine == generation else { return }          // cancelled while asking
            guard allowed else {
                deliver(.failed("Allow the microphone for this app in the watch's Settings."))
                return
            }
            do {
                let session = AVAudioSession.sharedInstance()
                try session.setCategory(.record, mode: .default, options: [])
                try session.setActive(true)
                tap = try MicTap(writingTo: url)
                isListening = true; heardSpeech = false; floorDB = -50; elapsed = 0
                levels = Array(repeating: 0, count: Self.bars)
                RuntimeKeeper.shared.hold("listen")
                WKInterfaceDevice.current().play(.start)       // the microphone is open
                startedAt = Date(); lastSpeech = Date()
                loop = Task {
                    while !Task.isCancelled {
                        try? await Task.sleep(for: .milliseconds(50))
                        self.tick()
                    }
                }
            } catch {
                deliver(.failed("The microphone isn't available."))
            }
        }
    }

    /// Ends the recording now (Stop): keeps what was said, if anything.
    public func send() { end() }

    /// Discards the recording.
    public func cancel() { stop(deliver: false) }

    private func tick() {
        guard let db = tap?.latestDB else { return }
        elapsed = Date().timeIntervalSince(startedAt)
        // The first half second sets the room's floor; it then follows the quietest moments slowly.
        if elapsed < 0.5 { floorDB = min(floorDB == -50 ? db : floorDB, db) }
        else if db < floorDB { floorDB = 0.9 * floorDB + 0.1 * db }
        let speaking = elapsed > 0.5 && db > max(floorDB + 12, -48)
        if speaking {
            if !heardSpeech { heardSpeech = true }
            lastSpeech = Date()
        }
        levels.removeFirst(); levels.append(min(1, max(0, (db + 60) / 50)))
        let quiet = Date().timeIntervalSince(lastSpeech)
        if quiet >= (heardSpeech ? Self.silenceToSend : Self.waitForSpeech) || elapsed >= Self.maxDuration { end() }
    }

    private func end() {
        guard isListening else { return }
        let spoke = heardSpeech
        let duration = Date().timeIntervalSince(startedAt)
        tap?.stop(); tap = nil                    // closes the file (its WAV header is written then)
        let data = spoke ? (try? Data(contentsOf: url)) : nil
        stop(deliver: false, keepCallback: true)
        if let data, data.count > 44 { deliver(.speech(data, duration: duration)) } else { deliver(.nothingHeard) }
    }

    private func stop(deliver shouldDeliver: Bool, keepCallback: Bool = false) {
        generation += 1
        loop?.cancel(); loop = nil
        tap?.stop(); tap = nil
        if isListening { isListening = false }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        RuntimeKeeper.shared.release("listen")
        if !shouldDeliver && !keepCallback { finish = nil }
    }

    private func deliver(_ outcome: Outcome) {
        let f = finish; finish = nil
        f?(outcome)
    }
}

/// The microphone through AVAudioEngine: each buffer's loudness (dBFS, RMS) is kept for speech
/// detection and the waveform, and the audio is written as 16 kHz mono 16-bit WAV. The tap runs on
/// the audio thread; `latestDB` is read from the main actor under a lock.
final class MicTap: @unchecked Sendable {
    private let engine = AVAudioEngine()
    private var file: AVAudioFile?
    private let converter: AVAudioConverter
    private let outFormat: AVAudioFormat
    private let lock = NSLock()
    private var db: Double?

    var latestDB: Double? { lock.withLock { db } }

    init(writingTo url: URL) throws {
        let input = engine.inputNode
        let inFormat = input.outputFormat(forBus: 0)
        guard inFormat.sampleRate > 0, inFormat.channelCount > 0,
              let out = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true),
              let conv = AVAudioConverter(from: inFormat, to: out) else { throw CocoaError(.fileWriteUnknown) }
        outFormat = out
        converter = conv
        try? FileManager.default.removeItem(at: url)
        file = try AVAudioFile(forWriting: url, settings: out.settings, commonFormat: .pcmFormatInt16, interleaved: true)
        input.installTap(onBus: 0, bufferSize: 1024, format: inFormat) { [weak self] buffer, _ in
            self?.take(buffer)
        }
        engine.prepare()
        try engine.start()
    }

    private func take(_ buffer: AVAudioPCMBuffer) {
        if let ch = buffer.floatChannelData?[0], buffer.frameLength > 0 {
            let n = Int(buffer.frameLength)
            var sum: Float = 0
            for i in 0..<n { sum += ch[i] * ch[i] }
            let rms = sqrt(sum / Float(n))
            let value = 20 * log10(Double(max(rms, 1e-7)))
            lock.withLock { db = value }
        }
        let ratio = outFormat.sampleRate / buffer.format.sampleRate
        guard let out = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 32) else { return }
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true; status.pointee = .haveData; return buffer
        }
        if error == nil, out.frameLength > 0 { lock.withLock { try? file?.write(from: out) } }
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        lock.withLock { file = nil }
    }
}
#endif
