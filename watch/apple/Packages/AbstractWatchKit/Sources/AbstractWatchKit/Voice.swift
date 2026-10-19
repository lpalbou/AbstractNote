#if os(watchOS)
import GatewayCore
import SwiftUI

/// The round microphone button beside a text field: one deliberate tap starts recording.
public struct MicButton: View {
    let action: () -> Void
    public init(action: @escaping () -> Void) { self.action = action }
    public var body: some View {
        Button(action: action) {
            Image(systemName: "mic.fill")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(Theme.text)
                .frame(width: 40, height: 40)
                .background(Theme.indigoButton, in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Record")
        .accessibilityHint("Records your voice; stops by itself after 3 seconds of silence")
        .accessibilityIdentifier("mic")
    }
}

/// The recording state: a waveform, the elapsed time, Stop (keeps it) and Cancel (drops it).
/// `levels` are recent loudness values (0…1), newest last.
public struct ListeningView: View {
    let levels: [Double]
    let elapsed: TimeInterval
    let heardSpeech: Bool
    let stop: () -> Void
    let cancel: () -> Void

    public init(levels: [Double], elapsed: TimeInterval, heardSpeech: Bool, stop: @escaping () -> Void, cancel: @escaping () -> Void) {
        self.levels = levels; self.elapsed = elapsed; self.heardSpeech = heardSpeech; self.stop = stop; self.cancel = cancel
    }

    public var body: some View {
        VStack(spacing: 8) {
            HStack(spacing: 6) {
                Circle().fill(Theme.red).frame(width: 8, height: 8).accessibilityHidden(true)
                Text("Recording").font(.rounded(.footnote, weight: .semibold)).foregroundStyle(Theme.text)
                Spacer(minLength: 2)
                Text(Self.clock(elapsed)).font(.rounded(.footnote).monospacedDigit()).foregroundStyle(Theme.text2)
                    .accessibilityLabel("\(Int(elapsed)) seconds")
            }
            HStack(alignment: .center, spacing: 3) {
                ForEach(levels.indices, id: \.self) { i in
                    Capsule().fill(Theme.cyan.opacity(0.35 + 0.65 * levels[i]))
                        .frame(width: 4, height: max(4, 44 * levels[i]))
                }
            }
            .frame(height: 48)
            .animation(.linear(duration: 0.05), value: levels)
            .accessibilityHidden(true)
            Text(heardSpeech ? "Stops after 3 seconds of silence." : "Speak now.")
                .font(.rounded(.caption2)).foregroundStyle(Theme.muted)
            Spacer(minLength: 4)
            Button("Stop", action: stop)
                .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                .accessibilityHint("Keeps what you said")
                .accessibilityIdentifier("listen.stop")
            Button("Cancel", action: cancel)
                .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
                .accessibilityHint("Drops the recording")
        }
        .accessibilityElement(children: .contain)
    }

    /// 0:07, 1:00
    public static func clock(_ t: TimeInterval) -> String {
        let s = max(0, Int(t))
        return "\(s / 60):" + String(format: "%02d", s % 60)
    }
}

/// One sentence for a failure, with the one action that helps (Sign in, or Try again).
public struct ProblemView: View {
    let problem: Problem
    let signIn: () -> Void
    let retry: () -> Void

    public init(problem: Problem, signIn: @escaping () -> Void, retry: @escaping () -> Void) {
        self.problem = problem; self.signIn = signIn; self.retry = retry
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ErrorLine(text: problem.sentence).accessibilityIdentifier("problem")
            switch problem.action {
            case .signIn:
                Button("Sign in", action: signIn).buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                    .accessibilityIdentifier("problem.signin")
            case .retry:
                Button("Try again", action: retry).buttonStyle(FilledButtonStyle(fill: Theme.neutralButton))
                    .accessibilityIdentifier("problem.retry")
            case .none:
                EmptyView()
            }
        }
    }
}
#endif
