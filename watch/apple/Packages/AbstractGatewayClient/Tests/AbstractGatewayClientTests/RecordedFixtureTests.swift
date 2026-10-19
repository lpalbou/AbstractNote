import Foundation
import Testing
@testable import AbstractGatewayClient

/// Check S5 (review 01): the client must read what a REAL gateway answers, status codes included.
/// `Fixtures/recorded/` holds answers recorded from the hermetic gateway (lane W2); they are
/// replayed through the live client below. When the recordings folder of the repository
/// (`abstractnote/untracked/fixtures/gateway/`, or `$ABSTRACTNOTE_GATEWAY_FIXTURES`) exists, its
/// files must match the bundled copies and every one must be readable or explicitly not called.
enum RecordedFixtures {
    static var liveDirectory: URL {
        if let env = ProcessInfo.processInfo.environment["ABSTRACTNOTE_GATEWAY_FIXTURES"], !env.isEmpty { return URL(filePath: env) }
        // This file → abstractnote/ is seven levels up.
        var u = URL(filePath: #filePath)
        for _ in 0..<7 { u.deleteLastPathComponent() }
        return u.appending(path: "untracked/fixtures/gateway")
    }

    static func files(in dir: URL) -> [URL] {
        ((try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    static var liveFiles: [URL] { files(in: liveDirectory) }
    static var liveAvailable: Bool { !liveFiles.isEmpty }

    static func bundled() throws -> [String: Fixture] {
        let dir = try #require(Bundle.module.url(forResource: "Fixtures", withExtension: nil)).appending(path: "recorded")
        var out: [String: Fixture] = [:]
        for url in files(in: dir) { out[url.deletingPathExtension().lastPathComponent] = try Fixture(json: try Data(contentsOf: url), name: url.lastPathComponent) }
        return out
    }

    /// Paths the gateway answers that the watch never calls (the web app and the CLI do); each one
    /// covers itself and everything below it.
    static let notCalledByWatch = ["/api/gateway/note/status", "/api/gateway/note/entities", "/api/gateway/note/graph",
                                   "/api/gateway/note/queue", "/api/gateway/note/settings", "/api/gateway/note/processor/ensure",
                                   "/api/gateway/note/review", "/api/gateway/note/claims", "/api/gateway/note/day",
                                   "/api/gateway/automations"]

    /// Decodes one recorded answer with the model the client uses for that path and status.
    static func check(_ f: Fixture) throws {
        let path = f.path.split(separator: "?").first.map(String.init) ?? f.path
        if (400..<500).contains(f.status) {
            #expect(GatewayErrorBody.parse(String(decoding: f.body, as: UTF8.self))?.message != nil, "\(path) \(f.status): error body unreadable")
            return
        }
        guard (200..<300).contains(f.status) else { Issue.record("\(path): status \(f.status) has no expectation in the watch client"); return }
        if notCalledByWatch.contains(where: { path == $0 || path.hasPrefix($0 + "/") }) { return }
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
        case "/api/gateway/attachments/upload": _ = try GatewayTransport.uploaded(f.body, sessionId: "s")
        case let p where p.hasPrefix("/api/gateway/note/insights/") && p.hasSuffix("/ack"): break
        case let p where p.hasPrefix("/api/gateway/note/tasks/") && p.hasSuffix("/close"): break
        case let p where p.hasPrefix("/api/gateway/note/notes/"): _ = try d.decode(NoteDetail.self, from: f.body)
        case let p where p.hasPrefix("/api/gateway/runs/"):
            let run = try d.decode(RunSummary.self, from: f.body)
            if run.status == "completed" {
                switch run.workflowId ?? "" {
                case let w where w.contains("note-capture"): _ = try LiveGatewayClient.progress(run, as: CaptureAnswer.self)
                case let w where w.contains("note-recall"): _ = try LiveGatewayClient.progress(run, as: RecallAnswer.self)
                default: Issue.record("\(path): completed run of \(run.workflowId ?? "?") has no output model")
                }
            }
        default:
            Issue.record("\(path): no decoder in the watch client for this recorded path")
        }
    }
}

/// The recorded answers, replayed through the live client the app uses.
extension ReplayGroup {
    @Suite struct RecordedGatewayTests {
        private let rec: [String: Fixture]
        init() throws { rec = try RecordedFixtures.bundled() }

        private func serve(_ names: String...) throws { ReplayStub.reset(try names.map { try #require(rec[$0], "missing recording \($0)") }) }

        private let capture = Capture(text: "Met Dana about the LUT budget; she wants the Pareto by Friday.", occurredAt: .now, location: nil,
                                      idempotencyKey: "key-1", sessionId: "note-watch-1")

        @Test func everyRecordingIsReadableAndTheWatchesCallsAreCovered() throws {
            for f in rec.values { try RecordedFixtures.check(f) }
            let seen = Set(rec.values.map { "\($0.status) \($0.path.hasPrefix("/api/gateway/runs/") && $0.path != "/api/gateway/runs/start" ? "/api/gateway/runs/{id}" : $0.path)" })
            for need in ["200 /api/gateway/runs/start", "200 /api/gateway/runs/{id}", "200 /api/gateway/note/changes", "200 /api/gateway/note/cues",
                         "200 /api/gateway/note/tasks", "200 /api/gateway/note/insights", "200 /api/gateway/note/overview"] {
                #expect(seen.contains(need), "no recording for \(need)")
            }
            for status in [401, 409, 413] { #expect(rec.values.contains { $0.status == status }, "no recorded \(status) answer") }
        }

        @Test func aCaptureRequestHasTheRecordedShapeIncludingTheSessionInsideInputData() async throws {
            try serve("runs_start_capture_200")
            let recordedRunId = try JSONDecoder().decode(StartRunResponse.self, from: try #require(rec["runs_start_capture_200"]).body).runId
            #expect(try await ReplayStub.client().startCapture(capture) == recordedRunId)
            let sent = try #require(ReplayStub.seen.first)
            let input = try #require(sent.body["input_data"] as? [String: Any])
            #expect(input["session_id"] as? String == "note-watch-1" && sent.body["session_id"] as? String == "note-watch-1")
            // Every top-level and input key the watch sends is one the recorded request carried.
            let url = try #require(Bundle.module.url(forResource: "runs_start_capture_200", withExtension: "json", subdirectory: "Fixtures/recorded"))
            let recorded = try #require(try JSONSerialization.jsonObject(with: try Data(contentsOf: url)) as? [String: Any])
            let rb = try #require((recorded["request"] as? [String: Any])?["body"] as? [String: Any])
            let rin = try #require(rb["input_data"] as? [String: Any])
            #expect(Set(sent.body.keys).isSubset(of: Set(rb.keys)))
            #expect(Set(input.keys).subtracting(rin.keys).isSubset(of: ["process_inline", "location"]), "unrecorded input keys: \(Set(input.keys).subtracting(rin.keys))")
        }

        @Test func aQuestionCarriesTheSessionInsideInputData() async throws {
            try serve("runs_start_recall_200")
            _ = try await ReplayStub.client().startRecall(question: "What did Dana ask for?", effort: .standard, sessionId: "note-watch-1")
            let input = try #require(ReplayStub.seen.first?.body["input_data"] as? [String: Any])
            #expect(input["session_id"] as? String == "note-watch-1" && input["question"] as? String == "What did Dana ask for?")
        }

        @Test func recordedCaptureRunsReadAsTheirAnswers() async throws {
            try serve("run_capture_completed_200", "run_capture_duplicate_200", "run_capture_path_refused")
            let c = ReplayStub.client()
            guard case .done(let saved) = try await c.captureProgress(runId: "a") else { Issue.record("capture"); return }
            #expect(saved.response == "Saved as event “Dana and the LUT budget”." && saved.status == "queued" && saved.noteId?.hasPrefix("nt_") == true)
            guard case .done(let dup) = try await c.captureProgress(runId: "b") else { Issue.record("duplicate"); return }
            #expect(dup.status == "duplicate")
            // A refused input completes with success false: its sentence, without "Runtime error: ".
            #expect(try await c.captureProgress(runId: "c") == .failed("Local file paths are not accepted here; upload the file as an attachment instead."))
        }

        @Test func aRecordedRecallReadsAsItsAnswer() async throws {
            try serve("run_recall_completed_200")
            guard case .done(let a) = try await ReplayStub.client().recallProgress(runId: "r") else { Issue.record("recall"); return }
            #expect(a.response == "You met Dana about the LUT budget; she wants the Pareto by Friday.")
        }

        @Test func todaysRecordedReadsDecodeThroughTheClient() async throws {
            try serve("note_changes_200", "note_cues_200", "note_tasks_200", "note_insights_200", "note_overview_200", "note_get_200")
            let c = ReplayStub.client()
            let ch = try await c.changes(since: nil)
            #expect(ch.notes.count == 2 && ch.entities.count == 4 && !ch.cursor.isEmpty)
            #expect(ch.supersededClaims.map(\.kind) == ["retracted"] && ch.supersededClaims.first?.text == "Dana participant LUT")
            #expect(try await c.cues(limit: 3).isEmpty)
            #expect(try await c.openLoops(limit: 5).first?.text == "Send Dana the Pareto by Friday")
            #expect(try await c.insights(limit: 5).isEmpty)
            #expect(try await c.overview().resurfacing.isEmpty)
            let n = try await c.note(id: "n")   // the recording carries keys the watch ignores (attachments, revision, insight, …)
            #expect(n.title == "Dana and the LUT budget" && n.insight == nil)
        }

        @Test func aRecordedCueCarriesItsSubjectAndTheNotesBehindIt() async throws {
            try serve("note_cues_evidence_200")
            let cue = try #require(try await ReplayStub.client().cues(limit: 3).first)
            #expect(cue.subject?.title == "Dana" && cue.subject?.kind == "entity")
            #expect(cue.evidenceNoteIds.count == 1 && cue.entityIds == ["ex:person-dana"])
        }

        @Test func aRecordedInsightNoteCarriesItsEvidence() async throws {
            try serve("note_get_insight_200")
            let n = try await ReplayStub.client().note(id: "i")
            #expect(n.kind == "insight" && n.insight?.kind == "reminder" && n.insight?.evidenceNoteIds.count == 1)
        }

        @Test func recordedRefusalsMapToTheirSentences() async throws {
            try serve("note_status_401")
            await #expect(throws: GatewayError.unauthorized) { try await ReplayStub.client().changes(since: nil) }
            try serve("automation_run_now_409_busy")
            do { try await ReplayStub.client().close(taskId: "t"); Issue.record("no 409") }
            catch GatewayError.conflict(let body) { #expect(GatewayErrorBody.parse(body)?.reasonCode == "automation_busy") }
            try serve("attachments_upload_413")
            await #expect(throws: GatewayError.tooLarge) { try await ReplayStub.client().startCapture(capture) }
            try serve("note_get_other_account_404")
            do { _ = try await ReplayStub.client().note(id: "n"); Issue.record("no 404") }
            catch GatewayError.http(let status, let body) {
                #expect(status == 404 && GatewayErrorBody.parse(body)?.message == "That note is not in your notebook.")
            }
        }
    }
}

@Test(.enabled(if: RecordedFixtures.liveAvailable, "the repository's recordings folder is absent (CI): the bundled copies are checked instead"))
func theRepositoryRecordingsMatchTheBundledCopiesAndAreReadable() throws {
    let bundled = try RecordedFixtures.bundled()
    var live: [String: Data] = [:]
    for url in RecordedFixtures.liveFiles {
        let data = try Data(contentsOf: url)
        live[url.deletingPathExtension().lastPathComponent] = data
        try RecordedFixtures.check(try Fixture(json: data, name: url.lastPathComponent))
    }
    for (name, f) in bundled {
        let l = try #require(live[name].map { try Fixture(json: $0, name: name) }, "\(name) is no longer recorded")
        #expect(l.body == f.body && l.status == f.status && l.path == f.path, "\(name) drifted: copy the new recording into Fixtures/recorded")
    }
}

/// Strict on what the watch uses, blind to what it does not: an added key is ignored, a missing
/// used key fails.
@Test func addedKeysAreIgnoredAndMissingUsedKeysFail() throws {
    let d = JSONDecoder()
    let cue = try d.decode(Cue.self, from: Data(#"{"cue_id":"c","text":"t","evidence_note_ids":[],"entity_ids":[],"score":0.4,"new_field":{"a":1}}"#.utf8))
    #expect(cue.cueId == "c" && cue.subject == nil)
    #expect(throws: (any Error).self) { try d.decode(Cue.self, from: Data(#"{"cue_id":"c","text":"t","entity_ids":[]}"#.utf8)) }
    #expect(throws: (any Error).self) { try d.decode(NoteDetail.self, from: Data(#"{"note_id":"n","title":"x"}"#.utf8)) }
    #expect(Optional("  ").shown == nil && Optional("Dana").shown == "Dana" && (nil as String?).shown == nil)
}

@Test func runErrorsLoseTheRuntimePrefix() {
    #expect(Problem.sentence("Runtime error: Model is busy.") == "Model is busy.")
    #expect(Problem.sentence("Model is busy.") == "Model is busy.")
    #expect(Problem.sentence("  ") == nil && Problem.sentence(nil) == nil)
}
