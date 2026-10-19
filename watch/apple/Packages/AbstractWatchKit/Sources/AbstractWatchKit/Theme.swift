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
            .padding(.horizontal, 10).padding(.vertical, 8)
            .background(fill, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
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
            .frame(maxWidth: .infinity, minHeight: 40)
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
#endif
