#if os(watchOS)
import GatewayCore
import SwiftUI
import WatchKit

/// First launch, after Sign out, or when a session ends: who you are, then your token or an
/// emailed one-time code. A user name asks for its token; a registered email gets an 8-digit code
/// by email. Adapted from the AbstractAssistant watch's sign-in. `onSignedIn` keeps the result (in
/// AbstractNote: the sealed sign-in file, ADR 0006, never UserDefaults); it throws when it cannot.
public struct SignInView<Mark: View>: View {
    enum Step: Equatable { case identify, token(user: String), code(email: String, sentTo: String) }

    let appName: String
    let notice: String?
    let mark: Mark
    let newSessionId: () -> String
    let onSignedIn: (SavedSignIn) throws -> Void
    @State private var testIdentify: String?
    @State private var testSecret: String?
    @State private var step: Step = .identify
    @State private var identifier = UserDefaults.standard.string(forKey: SignInDefaults.lastUser) ?? ""
    @State private var secret = ""
    @State private var gatewayText = UserDefaults.standard.string(forKey: SignInDefaults.lastGateway) ?? SavedSignIn.defaultGateway
    @State private var editingGateway = false
    @State private var working = false
    @State private var error: String?

    /// `testIdentify` / `testSecret`: sign in without typing (debug builds' launch arguments).
    public init(appName: String, notice: String?, testIdentify: String? = nil, testSecret: String? = nil,
                newSessionId: @escaping () -> String, onSignedIn: @escaping (SavedSignIn) throws -> Void, @ViewBuilder mark: () -> Mark) {
        self.appName = appName; self.notice = notice; self.newSessionId = newSessionId; self.onSignedIn = onSignedIn; self.mark = mark()
        _testIdentify = State(initialValue: testIdentify); _testSecret = State(initialValue: testSecret)
    }

    public var body: some View {
        FitScroll {
            VStack(spacing: 6) {
                Spacer(minLength: 0)
                mark
                Text(appName)
                    .font(.rounded(.headline, weight: .bold))
                    .foregroundStyle(LinearGradient(colors: [Theme.cyan, Theme.indigo], startPoint: .leading, endPoint: .trailing))
                if let notice, step == .identify {
                    Text(notice).font(.rounded(.caption2)).foregroundStyle(Theme.amber).multilineTextAlignment(.center)
                }
                Spacer(minLength: 4)
                switch step {
                case .identify: identify
                case .token(let user): tokenStep(user)
                case .code(_, let to): codeStep(to)
                }
                if let error { ErrorLine(text: error) }
            }
            .padding(.horizontal, 2)
        }
        .background(
            RadialGradient(colors: [Theme.indigoButton.opacity(0.22), Theme.ground], center: .top, startRadius: 4, endRadius: 190)
                .ignoresSafeArea()
        )
        .task { await runTestHooks() }
    }

    // MARK: - Steps

    private var identify: some View {
        VStack(spacing: 5) {
            InputRow(placeholder: "Sign in with user or email", text: $identifier, working: working,
                     ready: !identifier.trimmingCharacters(in: .whitespaces).isEmpty && gatewayURL != nil) {
                Task { await proceed() }
            }
            gatewayRow
        }
    }

    private func tokenStep(_ user: String) -> some View {
        VStack(spacing: 5) {
            contextLine("Token for \(user)")
            InputRow(placeholder: "Token", text: $secret, secure: true, working: working,
                     ready: !secret.trimmingCharacters(in: .whitespaces).isEmpty) {
                Task { await signInWithToken(user) }
            }
        }
    }

    private func codeStep(_ sentTo: String) -> some View {
        VStack(spacing: 5) {
            contextLine("Code sent to \(sentTo)", extra: ("Resend", { Task { await proceed() } }))
            InputRow(placeholder: "8-digit code", text: $secret, working: working, ready: secret.filter(\.isNumber).count == 8) {
                Task { await signInWithCode() }
            }
        }
    }

    private func contextLine(_ text: String, extra: (String, () -> Void)? = nil) -> some View {
        VStack(spacing: 2) {
            Text(text).foregroundStyle(Theme.text2).lineLimit(1).minimumScaleFactor(0.6)
            HStack(spacing: 14) {
                Button("Change") { step = .identify; secret = ""; error = nil }
                if let (label, action) = extra { Button(label, action: action).disabled(working) }
            }
            .foregroundStyle(Theme.cyan)
        }
        .font(.rounded(.caption2))
        .buttonStyle(.plain)
    }

    private var gatewayRow: some View {
        Group {
            if editingGateway {
                TextField("Server", text: $gatewayText)
                    .textFieldStyle(.plain).multilineTextAlignment(.center)
                    .textContentType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                    .onSubmit { editingGateway = false }
            } else {
                Button { editingGateway = true } label: {
                    Text(gatewayURL?.host() ?? gatewayText).lineLimit(1).minimumScaleFactor(0.7)
                        .font(.rounded(.caption2)).foregroundStyle(Theme.muted.opacity(0.8))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Server \(gatewayURL?.host() ?? gatewayText)")
                .accessibilityHint("Change the server")
            }
        }
        .padding(.horizontal, 18)
    }

    private var gatewayURL: URL? { SavedSignIn.normalize(gatewayText) }

    // MARK: - Actions

    private func proceed() async {
        let id = identifier.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let gw = gatewayURL, !id.isEmpty else { return }
        error = nil; secret = ""
        guard GatewaySignIn.isEmail(id) else { step = .token(user: id); return }
        working = true
        defer { working = false }
        do {
            let answer = try await GatewaySignIn.requestCode(gateway: gw, identifier: id)
            if answer.sent {
                step = .code(email: id, sentTo: answer.to ?? "your email")
                WKInterfaceDevice.current().play(.success)
            } else {
                step = .identify
                error = "No code could be sent. Use your user name."
            }
        } catch {
            self.error = Problem.of(error, host: gw.host() ?? gw.absoluteString).sentence
        }
    }

    private func signInWithToken(_ user: String) async {
        guard let gw = gatewayURL else { return }
        let token = secret.trimmingCharacters(in: .whitespacesAndNewlines)
        working = true; error = nil
        defer { working = false }
        do {
            let me = try await GatewaySignIn.verify(gateway: gw, user: user, token: token)
            finish(SavedSignIn(gateway: gw, user: me.userId ?? user, credential: .token(token), sessionId: newSessionId()))
        } catch {
            self.error = Problem.of(error, host: gw.host() ?? gw.absoluteString).sentence
            WKInterfaceDevice.current().play(.failure)
        }
    }

    private func signInWithCode() async {
        guard let gw = gatewayURL, case .code(let email, _) = step else { return }
        working = true; error = nil
        defer { working = false }
        do {
            let (cred, me) = try await GatewaySignIn.redeem(gateway: gw, identifier: email, code: secret.filter(\.isNumber))
            finish(SavedSignIn(gateway: gw, user: me.userId ?? email, credential: cred, sessionId: newSessionId()))
        } catch {
            self.error = Problem.of(error, host: gw.host() ?? gw.absoluteString).sentence
            WKInterfaceDevice.current().play(.failure)
        }
    }

    private func finish(_ s: SavedSignIn) {
        do {
            try onSignedIn(s)
            WKInterfaceDevice.current().play(.success)
        } catch {
            self.error = "Your sign-in could not be saved on the watch."
            WKInterfaceDevice.current().play(.failure)
        }
    }

    /// Simulator/device checks without typing: `--test-identify <user or email>` then `--test-secret <token or code>`.
    private func runTestHooks() async {
        guard let id = testIdentify else { return }
        testIdentify = nil
        identifier = id
        await proceed()
        if let s = testSecret {
            testSecret = nil
            secret = s
            switch step {
            case .token(let user): await signInWithToken(user)
            case .code: await signInWithCode()
            case .identify: break
            }
        }
    }
}

/// A field with its send arrow inside, on the right: the label is the placeholder, the arrow is the
/// only button (enabled when the field is ready; a spinner while working).
public struct InputRow: View {
    let placeholder: String
    @Binding var text: String
    var secure = false
    var working = false
    var ready = true
    let send: () -> Void

    public init(placeholder: String, text: Binding<String>, secure: Bool = false, working: Bool = false, ready: Bool = true, send: @escaping () -> Void) {
        self.placeholder = placeholder; _text = text; self.secure = secure; self.working = working; self.ready = ready; self.send = send
    }

    public var body: some View {
        HStack(spacing: 4) {
            // A text-field link, not a TextField: the system field draws its own grey capsule inside
            // ours. The link opens the same input (dictation, Scribble, keyboard).
            TextFieldLink(prompt: Text(placeholder)) {
                Group {
                    if text.isEmpty { Text(placeholder).foregroundStyle(Theme.muted) }
                    else { Text(secure ? String(repeating: "•", count: min(text.count, 12)) : text).foregroundStyle(Theme.text) }
                }
                .font(.rounded(.footnote)).lineLimit(1).minimumScaleFactor(0.7)
                .frame(maxWidth: .infinity, minHeight: 28, alignment: .leading)
                .contentShape(Rectangle())
            } onSubmit: { value in
                text = value.trimmingCharacters(in: .whitespacesAndNewlines)
                if !text.isEmpty && !working { DispatchQueue.main.async { send() } }
            }
            .buttonStyle(.plain)
            .accessibilityLabel(placeholder)
            Button(action: send) {
                Group {
                    if working { ProgressView() } else { Image(systemName: "arrow.up.circle.fill").font(.system(size: 24)) }
                }
                .frame(width: 28, height: 28)
                .foregroundStyle(ready ? Theme.cyan : Theme.muted.opacity(0.5))
            }
            .buttonStyle(.plain)
            .disabled(!ready || working)
            .accessibilityLabel("Continue")
        }
        .padding(.leading, 12).padding(.trailing, 4).padding(.vertical, 4)
        .background(Theme.card, in: Capsule())
    }
}

/// Not secret: the last server and account typed, as the next sign-in's defaults (the server is
/// kept on Sign out).
public enum SignInDefaults {
    public static let lastGateway = "signin.lastGateway"
    public static let lastUser = "signin.lastUser"
    public static func remember(_ s: SavedSignIn) {
        UserDefaults.standard.set(s.gateway.absoluteString, forKey: lastGateway)
        UserDefaults.standard.set(s.user, forKey: lastUser)
    }
}
#endif
