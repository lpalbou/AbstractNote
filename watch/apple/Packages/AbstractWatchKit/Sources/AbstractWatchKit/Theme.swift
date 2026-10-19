#if os(watchOS)
import GatewayCore
import SwiftUI

// The look of the AbstractFramework watch apps (the AbstractAssistant watch's Theme.swift, trimmed):
// the palette, cards, filled buttons and the FitScroll layout rule (actions at the bottom).

extension Color {
    public init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255, blue: Double(hex & 0xFF) / 255)
    }
}

public enum Theme {
    public static let ground = Color(hex: 0x0a0a12)
    public static let card = Color(hex: 0x15151f)
    public static let text = Color(hex: 0xeceef5)
    public static let text2 = Color(hex: 0xc4c6d4)
    public static let muted = Color(hex: 0x8f91a6)
    public static let indigo = Color(hex: 0x818cf8)
    public static let indigoButton = Color(hex: 0x4f46e5)
    public static let cyan = Color(hex: 0x22d3ee)
    public static let green = Color(hex: 0x34d399)
    public static let greenButton = Color(hex: 0x059669)
    public static let amber = Color(hex: 0xfbbf24)
    public static let red = Color(hex: 0xf87171)
    public static let neutralButton = Color(hex: 0x1c1c28)
    /// A state colour (always beside a word): organized, done.
    public static let mint = Color(hex: 0x5eead4)
    /// A raised surface inside a card (chips, the text field).
    public static let raised = Color(hex: 0x20202d)
}

/// The 8 pt grid: every gap and padding is one of these.
public enum Space {
    public static let xs: CGFloat = 4
    public static let s: CGFloat = 8
    public static let m: CGFloat = 16
    public static let l: CGFloat = 24
}

/// The layout rule for screens with actions: content from the top, actions pinned to the bottom
/// when everything fits on one screen (a Spacer before the actions does it); a longer screen
/// scrolls as usual, the actions after the content.
public struct FitScroll<Content: View>: View {
    @ViewBuilder let content: () -> Content
    public init(@ViewBuilder content: @escaping () -> Content) { self.content = content }
    public var body: some View {
        GeometryReader { geo in
            ScrollView {
                content().frame(maxWidth: .infinity, minHeight: geo.size.height, alignment: .top)
            }
            .scrollBounceBehavior(.basedOnSize)
        }
    }
}

extension Font {
    /// The system text styles (they follow the watch's text size, Dynamic Type), in the rounded design.
    public static func rounded(_ style: Font.TextStyle, weight: Font.Weight = .regular) -> Font {
        .system(style, design: .rounded, weight: weight)
    }
}

public struct Card<Content: View>: View {
    var fill: Color = Theme.card
    @ViewBuilder var content: Content
    public init(fill: Color = Theme.card, @ViewBuilder content: () -> Content) { self.fill = fill; self.content = content() }
    public var body: some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(fill, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

public struct FilledButtonStyle: ButtonStyle {
    let fill: Color
    var foreground: Color = Theme.text
    public init(fill: Color, foreground: Color = Theme.text) { self.fill = fill; self.foreground = foreground }
    public func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.rounded(.body, weight: .semibold)).lineLimit(1).minimumScaleFactor(0.75)
            .padding(.horizontal, 12).padding(.vertical, 4)
            .foregroundStyle(foreground)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background(fill.opacity(configuration.isPressed ? 0.7 : 1), in: Capsule())
            .modifier(DimWhenDisabled())
    }
}

private struct DimWhenDisabled: ViewModifier {
    @Environment(\.isEnabled) private var enabled
    func body(content: Content) -> some View { content.opacity(enabled ? 1 : 0.45) }
}

extension View {
    /// Dark ground behind every screen, the title inline.
    public func screen(_ title: String) -> some View {
        self.navigationTitle(title)
            .toolbarTitleDisplayMode(.inline)
            .containerBackground(Theme.ground, for: .navigation)
    }
}

/// "Loading" with three dots that fill in turn: what a line says until its information is in.
public struct LoadingText: View {
    var text = "Loading"
    public init(text: String = "Loading") { self.text = text }
    public var body: some View {
        TimelineView(.periodic(from: .now, by: 0.4)) { ctx in
            let n = Int(ctx.date.timeIntervalSinceReferenceDate / 0.4) % 4
            HStack(spacing: 0) {
                Text(text)
                ForEach(0..<3, id: \.self) { i in Text(".").opacity(i < n ? 1 : 0) }   // the width never moves
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text)
    }
}

/// A section heading in a list screen.
public struct SectionTitle: View {
    let text: String
    public init(text: String) { self.text = text }
    public var body: some View {
        Text(text).font(.rounded(.caption2, weight: .semibold)).foregroundStyle(Theme.muted)
            .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 6)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A one-sentence error in red with an icon (never colour alone).
public struct ErrorLine: View {
    let text: String
    public init(text: String) { self.text = text }
    public var body: some View {
        Label(text, systemImage: "exclamationmark.circle")
            .font(.rounded(.footnote)).foregroundStyle(Theme.red).multilineTextAlignment(.leading)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// "12 Oct" or "12 Oct 2025": when a note was written, in the watch's own words.
public enum When {
    public static func short(_ iso: String?) -> String? {
        guard let iso, let d = ISO8601.date(iso) else { return nil }
        let sameYear = Calendar.current.isDate(d, equalTo: .now, toGranularity: .year)
        return d.formatted(sameYear ? .dateTime.day().month(.abbreviated) : .dateTime.day().month(.abbreviated).year())
    }
}
/// A small label in a capsule: a note's kind, an entity's kind ("idea", "person").
public struct Pill: View {
    let text: String
    var color: Color = Theme.text2
    public init(_ text: String, color: Color = Theme.text2) { self.text = text; self.color = color }
    public var body: some View {
        Text(text).font(.rounded(.caption2, weight: .semibold)).lineLimit(1)
            .foregroundStyle(color)
            .padding(.horizontal, Space.s).padding(.vertical, 2)
            .background(Theme.raised, in: Capsule())
            .fixedSize()
    }
}

/// A tappable name in a capsule (an entity linked to a note).
public struct Chip: View {
    let text: String
    var symbol: String?
    public init(_ text: String, symbol: String? = nil) { self.text = text; self.symbol = symbol }
    public var body: some View {
        HStack(spacing: Space.xs) {
            if let symbol { Image(systemName: symbol).font(.caption2).foregroundStyle(Theme.indigo) }
            Text(text).font(.rounded(.footnote, weight: .medium)).foregroundStyle(Theme.text).lineLimit(1)
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(Theme.raised, in: Capsule())
    }
}

/// A state as one line: a symbol and its word, the colour only beside the word.
public struct StateLine: View {
    let symbol: String
    let text: String
    var color: Color = Theme.text2
    public init(symbol: String, text: String, color: Color = Theme.text2) { self.symbol = symbol; self.text = text; self.color = color }
    public var body: some View {
        Label { Text(text).foregroundStyle(Theme.text) } icon: { Image(systemName: symbol).foregroundStyle(color) }
            .font(.rounded(.footnote, weight: .medium))
            .accessibilityElement(children: .combine)
    }
}

/// The large state of the moment (the deck's slide 09): a ring with its symbol, the word, a line.
public struct StatusHero: View {
    let symbol: String
    let color: Color
    let title: String
    let subtitle: String?
    var working = false
    public init(symbol: String, color: Color, title: String, subtitle: String? = nil, working: Bool = false) {
        self.symbol = symbol; self.color = color; self.title = title; self.subtitle = subtitle; self.working = working
    }
    public var body: some View {
        VStack(spacing: Space.s) {
            ZStack {
                Circle().strokeBorder(color, lineWidth: 3).frame(width: 52, height: 52)
                if working { ProgressView().tint(color) } else {
                    Image(systemName: symbol).font(.system(size: 22, weight: .semibold)).foregroundStyle(color)
                }
            }
            Text(title).font(.rounded(.title3, weight: .semibold)).foregroundStyle(Theme.text).multilineTextAlignment(.center)
            if let subtitle {
                Text(subtitle).font(.rounded(.footnote)).foregroundStyle(Theme.text2).multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }
}

/// A large rounded text area that opens the watch's input (dictation, Scribble, keyboard): the
/// system field would draw its own small capsule.
public struct TextArea: View {
    let prompt: String
    @Binding var text: String
    var minHeight: CGFloat = 72
    public init(prompt: String, text: Binding<String>, minHeight: CGFloat = 72) { self.prompt = prompt; _text = text; self.minHeight = minHeight }
    public var body: some View {
        TextFieldLink(prompt: Text(prompt)) {
            Group {
                if text.isEmpty { Text(prompt).foregroundStyle(Theme.muted) } else { Text(text).foregroundStyle(Theme.text) }
            }
            .font(.rounded(.body))
            .multilineTextAlignment(.leading)
            .frame(maxWidth: .infinity, minHeight: minHeight, alignment: .topLeading)
            .padding(Space.s + 4)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: 18))
        } onSubmit: { value in
            text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        .buttonStyle(.plain)
        .accessibilityValue(text.isEmpty ? "Empty" : text)
    }
}
#endif
