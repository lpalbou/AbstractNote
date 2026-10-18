import Foundation
import Testing
@testable import AbstractGatewayClient

/// Check S5 (review 01): the client must read what a REAL gateway answers, not only the
/// hand-written fixtures. Every file recorded from the hermetic gateway under
/// `abstractnote/untracked/fixtures/gateway/` (or `$ABSTRACTNOTE_GATEWAY_FIXTURES`) is decoded by
/// the model its request path names; a path with no model is a failure, not a skip. Without
/// recordings the test is SKIPPED (shown as skipped, never as passed).
enum RecordedFixtures {
    static var directory: URL {
        if let env = ProcessInfo.processInfo.environment["ABSTRACTNOTE_GATEWAY_FIXTURES"], !env.isEmpty { return URL(filePath: env) }
        // This file → abstractnote/ is seven levels up.
        var u = URL(filePath: #filePath)
        for _ in 0..<7 { u.deleteLastPathComponent() }
        return u.appending(path: "untracked/fixtures/gateway")
    }

    static var files: [URL] {
        ((try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    static var available: Bool { !files.isEmpty }

    /// Decodes one recorded answer with the model the client uses for that path and status.
    static func check(_ f: Fixture) throws {
        let path = f.path.split(separator: "?").first.map(String.init) ?? f.path
        switch f.status {
        case 401, 409, 413:
            #expect(GatewayErrorBody.parse(String(decoding: f.body, as: UTF8.self)) != nil, "\(path) \(f.status): error body unreadable")
            return
        case 200..<300: break
        default:
            Issue.record("\(path): status \(f.status) has no expectation in the watch client"); return
        }
        let d = JSONDecoder()
        switch path {
        case "/api/gateway/me": _ = try d.decode(Principal.self, from: f.body)
        case "/api/gateway/runs/start": _ = try d.decode(StartRunResponse.self, from: f.body)
        case "/api/gateway/note/changes": _ = try d.decode(NoteChanges.self, from: f.body)
        case "/api/gateway/note/overview": _ = try d.decode(NoteOverview.self, from: f.body)
        case "/api/gateway/note/cues": _ = try d.decode(ListPage<Cue>.self, from: f.body)
        case "/api/gateway/note/tasks": _ = try d.decode(ListPage<OpenLoop>.self, from: f.body)
        case "/api/gateway/note/insights": _ = try d.decode(ListPage<Insight>.self, from: f.body)
        case "/api/gateway/session/logout": break
        case let p where p.hasPrefix("/api/gateway/note/notes/"): _ = try d.decode(NoteDetail.self, from: f.body)
        case let p where p.hasPrefix("/api/gateway/note/insights/") && p.hasSuffix("/ack"): break
        case let p where p.hasPrefix("/api/gateway/note/tasks/") && p.hasSuffix("/close"): break
        case let p where p.hasPrefix("/api/gateway/runs/"):
            let run = try d.decode(RunSummary.self, from: f.body)
            if run.status == "completed" {
                switch run.workflowId ?? "" {
                case let w where w.contains("note-capture"): _ = try GatewayClientProgress.capture(run)
                case let w where w.contains("note-recall"): _ = try GatewayClientProgress.recall(run)
                default: Issue.record("\(path): completed run of \(run.workflowId ?? "?") has no output model")
                }
            }
        default:
            Issue.record("\(path): no decoder in the watch client for this recorded path")
        }
    }
}

/// The client's own output reading, reachable without a client.
enum GatewayClientProgress {
    static func capture(_ r: RunSummary) throws -> RunProgress<CaptureAnswer> { try LiveGatewayClient.progress(r, as: CaptureAnswer.self) }
    static func recall(_ r: RunSummary) throws -> RunProgress<RecallAnswer> { try LiveGatewayClient.progress(r, as: RecallAnswer.self) }
}

@Test(.enabled(if: RecordedFixtures.available, "no recordings yet in untracked/fixtures/gateway (lane W2): refresh S5 when they exist"))
func recordedGatewayAnswersDecode() throws {
    var statuses: Set<Int> = []
    for url in RecordedFixtures.files {
        let f = try Fixture(json: try Data(contentsOf: url), name: url.lastPathComponent)
        statuses.insert(f.status)
        try RecordedFixtures.check(f)
    }
    // Review 01 S5 asks for the refusals too.
    for s in [401, 409, 413] { #expect(statuses.contains(s), "no recorded \(s) answer") }
}

@Test func theShippedFixturesPassTheRecordedCheck() throws {
    // The same check over the hand-written fixtures: keeps it honest while no recording exists.
    let dir = try #require(Bundle.module.url(forResource: "Fixtures", withExtension: nil))
    let files = try FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil).filter { $0.pathExtension == "json" }
    #expect(files.count >= 15)
    for url in files { try RecordedFixtures.check(try Fixture(json: try Data(contentsOf: url), name: url.lastPathComponent)) }
}
