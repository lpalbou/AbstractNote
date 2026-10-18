import AbstractGatewayClient
import CoreLocation
import Foundation

/// Where a note was written, when the person allows it. Settings has the switch (on by default);
/// the system asks once, the first time the Note screen shows. A fix older than ten minutes is
/// not attached: a note never claims a place it was not written at.
@MainActor
final class Location: NSObject, ObservableObject {
    static let enabledKey = "location.on"
    static let maxAge: TimeInterval = 600

    @Published private(set) var authorization: CLAuthorizationStatus
    @Published var enabled: Bool {
        didSet {
            UserDefaults.standard.set(enabled, forKey: Self.enabledKey)
            if enabled { prepare() }
        }
    }
    private var last: CLLocation?
    private let manager = CLLocationManager()
    /// Screenshot and UI-test runs never put the system question over the screen.
    private let quiet: Bool

    init(quiet: Bool) {
        self.quiet = quiet
        authorization = manager.authorizationStatus
        enabled = UserDefaults.standard.object(forKey: Self.enabledKey) as? Bool ?? true
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    var allowed: Bool { authorization == .authorizedWhenInUse || authorization == .authorizedAlways }

    /// Asks once (the system remembers the answer), then takes a fresh fix for the next note.
    func prepare() {
        guard enabled, !quiet else { return }
        switch authorization {
        case .notDetermined: manager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse, .authorizedAlways: manager.requestLocation()
        default: break
        }
    }

    /// The place to attach to a note now, if any.
    var current: CaptureLocation? {
        guard enabled, allowed, let l = last, Date().timeIntervalSince(l.timestamp) < Self.maxAge else { return nil }
        return CaptureLocation(lat: l.coordinate.latitude, lon: l.coordinate.longitude)
    }

    /// What Settings says under the switch.
    var status: String {
        if !enabled { return "Notes are sent without a place." }
        switch authorization {
        case .denied, .restricted: return "Allow Location for Note in the watch's Settings."
        case .notDetermined: return "The watch asks the first time you write a note."
        default: return "Notes keep where you wrote them."
        }
    }
}

extension Location: CLLocationManagerDelegate {
    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        Task { @MainActor in
            self.authorization = status
            if self.allowed && self.enabled { self.manager.requestLocation() }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let l = locations.last else { return }
        Task { @MainActor in self.last = l }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        // No fix: the note goes without a place (said nowhere: a place is optional).
    }
}
