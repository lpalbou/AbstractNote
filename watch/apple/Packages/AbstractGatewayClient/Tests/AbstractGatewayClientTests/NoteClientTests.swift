import Foundation
import Testing
@testable import AbstractGatewayClient

/// The live client against the fixtures (status codes included): what it sends and how it reads
/// every answer. Serialized: the replay stub's queue is shared.
@Suite(.serialized) struct NoteClientTests {
    private func load(_ names: String...) throws { ReplayStub.reset(try names.map(Fixture.named)) }

    private let capture = Capture(text: "Met Dana about the LUT budget", occurredAt: Date(timeIntervalSince1970: 1_760_865_120),
                                  location: CaptureLocation(lat: 48.85, lon: 2.35), idempotencyKey: "key-1", sessionId: "note-watch-1")

    @Test func aCaptureStartsNoteCaptureWithItsKeySessionAndNoInlineProcessing() async throws {
        try load("runs_start")
        let runId = try await ReplayStub.client().startCapture(capture)
        #expect(runId == "run-cap-1")
        let req = try #require(ReplayStub.seen.first)
        #expect(req.method == "POST" && req.path == "/api/gateway/runs/start")
        #expect(req.body["bundle_id"] as? String == "abstractnote" && req.body["flow_id"] as? String == "note-capture")
        #expect(req.body["session_id"] as? String == "note-watch-1")
        let input = try #require(req.body["input_data"] as? [String: Any])
        #expect(input["text"] as? String == "Met Dana about the LUT budget")
        #expect(input["idempotency_key"] as? String == "key-1")
        #expect(input["process_inline"] as? Bool == false)
        #expect(input["occurred_at"] as? String == "2025-10-19T09:12:00Z")
        #expect((input["source"] as? [String: Any])?["client"] as? String == "watch")
        let loc = try #require(input["location"] as? [String: Any])
        #expect(loc["lat"] as? Double == 48.85 && loc["lon"] as? Double == 2.35)
        // Never the caller's identity: the gateway's routing picks the notebook.
        for key in ["user_id", "tenant_id", "runtime_id"] { #expect(input[key] == nil && req.body[key] == nil) }
        #expect(input["context"] == nil)
    }

    @Test func aRetryOfTheSameNoteSendsTheSameKey() async throws {
        try load("error_413", "runs_start")
        let c = ReplayStub.client()
        await #expect(throws: GatewayError.tooLarge) { try await c.startCapture(capture) }
        _ = try await c.startCapture(capture)
        let keys = ReplayStub.seen.map { ($0.body["input_data"] as? [String: Any])?["idempotency_key"] as? String }
        #expect(keys == ["key-1", "key-1"])
    }

    @Test func aNoteAboutSomethingCarriesItsSubject() async throws {
        try load("runs_start")
        var c = capture; c.about = NoteSubject(kind: "cue", id: "cue-1", title: "Marc")
        _ = try await ReplayStub.client().startCapture(c)
        let input = try #require(ReplayStub.seen.first?.body["input_data"] as? [String: Any])
        let about = try #require((input["context"] as? [String: Any])?["about"] as? [String: Any])
        #expect(about["kind"] as? String == "cue" && about["id"] as? String == "cue-1")
    }

    @Test func aCaptureIsFollowedToItsFilingAnswer() async throws {
        try load("run_capture_running", "run_capture_completed")
        let c = ReplayStub.client()
        let p = try await c.follow({ try await c.captureProgress(runId: "run-cap-1") }, every: .milliseconds(10), timeout: .seconds(5))
        #expect(p == .done(CaptureAnswer(response: "Saved as idea ‘LUT budget’. Related: Dana, LUT.", noteId: "n-41", status: "new")))
        #expect(ReplayStub.seen.map(\.path) == ["/api/gateway/runs/run-cap-1", "/api/gateway/runs/run-cap-1"])
    }

    @Test func aRepeatedKeyAnswersDuplicate() async throws {
        try load("run_capture_duplicate")
        let p = try await ReplayStub.client().captureProgress(runId: "run-cap-2")
        guard case .done(let a) = p else { Issue.record("not done: \(p)"); return }
        #expect(a.status == "duplicate" && a.noteId == "n-41")
    }

    @Test func aFailedOrWaitingRunIsSaidSo() async throws {
        try load("run_failed", "run_waiting_user")
        let c = ReplayStub.client()
        #expect(try await c.captureProgress(runId: "run-x") == .failed)
        #expect(try await c.captureProgress(runId: "run-w") == .needsPerson)
    }

    @Test func aCompletedRunWithoutTheDocumentedOutputFailsLoudly() async throws {
        ReplayStub.reset([try Fixture(json: Data(#"{"request":{"method":"GET","path":"/x"},"status":200,"body":{"run_id":"r","status":"completed","output":{"answer":"no response pin"}}}"#.utf8), name: "inline")])
        await #expect(throws: GatewayError.self) { try await ReplayStub.client().captureProgress(runId: "r") }
    }

    @Test func aQuestionAsksAtStandardAndReadsTheAnswerWithItsEvidence() async throws {
        try load("runs_start", "run_recall_completed")
        let c = ReplayStub.client()
        _ = try await c.startRecall(question: "What did I decide about the LUT budget?", effort: .standard, sessionId: "note-watch-1")
        let body = try #require(ReplayStub.seen.first?.body)
        #expect(body["flow_id"] as? String == "note-recall" && body["bundle_id"] as? String == "abstractnote")
        let input = try #require(body["input_data"] as? [String: Any])
        #expect(input["question"] as? String == "What did I decide about the LUT budget?" && input["effort"] as? String == "standard")
        guard case .done(let a) = try await c.recallProgress(runId: "run-rec-1") else { Issue.record("not done"); return }
        #expect(a.response == "You settled on 50k on 17 October.")
        #expect(a.evidence.map(\.title) == ["LUT budget with Dana", "Budget settled at 50k"])
    }

    @Test func todayReadsChangesSinceTheCursorCuesLoopsInsightsAndOnThisDay() async throws {
        try load("note_changes", "note_cues", "note_tasks", "note_insights", "note_overview")
        let c = ReplayStub.client()
        let ch = try await c.changes(since: "c-1")
        #expect(ch.cursor == "c-2026-10-19T09:00:00Z" && ch.notes.first?.title == "Pareto by Friday")
        #expect(ch.entities.first?.name == "Dana" && ch.supersededClaims.count == 1 && ch.closedLoops.first?.status == "done")
        #expect(try await c.cues(limit: 3).first?.cueId == "cue-1")
        #expect(try await c.openLoops(limit: 5).first?.taskId == "t-7")
        #expect(try await c.insights(limit: 5).first?.noteId == "i-1")
        #expect(try await c.overview().resurfacing.first?.title == "First sketch of the reading list")
        let s = ReplayStub.seen
        #expect(s.map(\.path) == ["/api/gateway/note/changes", "/api/gateway/note/cues", "/api/gateway/note/tasks",
                                  "/api/gateway/note/insights", "/api/gateway/note/overview"])
        #expect(s[0].query["since"] == "c-1")
        #expect(s[2].query["status"] == "open" && s[3].query["acked"] == "false")
    }

    @Test func changesWithoutACursorFailLoudly() async throws {
        ReplayStub.reset([try Fixture(json: Data(#"{"request":{"method":"GET","path":"/x"},"status":200,"body":{"notes":[]}}"#.utf8), name: "inline")])
        await #expect(throws: GatewayError.self) { try await ReplayStub.client().changes(since: nil) }
        #expect(ReplayStub.seen.first?.query["since"] == nil)
    }

    @Test func aNoteIsReadByItsId() async throws {
        try load("note_note")
        let n = try await ReplayStub.client().note(id: "n-12")
        #expect(n.title == "LUT budget with Dana" && n.text.hasPrefix("Met Dana"))
        #expect(ReplayStub.seen.first?.path == "/api/gateway/note/notes/n-12")
    }

    @Test func anIdNeverLeavesItsPathSegment() async throws {
        ReplayStub.reset([])
        await #expect(throws: GatewayError.invalidResponse) { try await ReplayStub.client().note(id: "../runs") }
        await #expect(throws: GatewayError.invalidResponse) { try await ReplayStub.client().close(taskId: "a/b") }
        #expect(ReplayStub.seen.isEmpty)
    }

    @Test func acknowledgingAndClosingAreWritesWithTheCsrfOfASession() async throws {
        try load("ack_ok", "error_409")
        let c = ReplayStub.client(.session(id: "s1", csrf: "c1"))
        try await c.acknowledge(insightId: "i-1")
        await #expect(throws: GatewayError.self) { try await c.close(taskId: "t-7") }
        #expect(ReplayStub.seen.map(\.method) == ["POST", "POST"])
        #expect(ReplayStub.seen.map(\.path) == ["/api/gateway/note/insights/i-1/ack", "/api/gateway/note/tasks/t-7/close"])
    }

    @Test func a409IsAlreadyDoneElsewhere() async throws {
        try load("error_409")
        do { try await ReplayStub.client().close(taskId: "t-7"); Issue.record("no error") }
        catch GatewayError.conflict(let body) {
            #expect(GatewayErrorBody.parse(body) == GatewayErrorBodyExpect.closed)
            #expect(GatewayError.conflict(body: body).errorDescription == "Already done on another device.")
        }
        #expect(ReplayStub.seen.count == 1)   // a refusal is never retried
    }

    @Test func a413IsTooLongToSend() async throws {
        try load("error_413")
        await #expect(throws: GatewayError.tooLarge) { try await ReplayStub.client().startCapture(capture) }
        #expect(GatewayError.tooLarge.errorDescription == "This is too long to send from the watch.")
    }

    @Test func a401LocksTheClientOutUntilTheNextSignIn() async throws {
        try load("error_401", "note_changes")
        let c = ReplayStub.client()
        await #expect(throws: GatewayError.unauthorized) { try await c.changes(since: nil) }
        await #expect(throws: GatewayError.unauthorized) { try await c.changes(since: nil) }
        #expect(ReplayStub.seen.count == 1)   // the second call never reached the gateway
    }

    @Test func signingOutOfASessionLogsItOutOnce() async throws {
        try load("ack_ok")
        let c = ReplayStub.client(.session(id: "s1", csrf: "c1"))
        await c.signOut()
        await c.signOut()
        #expect(ReplayStub.seen.map(\.path) == ["/api/gateway/session/logout"])
        ReplayStub.reset([])
        await ReplayStub.client(.token("t")).signOut()
        #expect(ReplayStub.seen.isEmpty)      // a token has no session to end
    }

    @Test func meReadsThePrincipal() async throws {
        try load("me")
        #expect(try await ReplayStub.client().me().userId == "alice")
    }
}

enum GatewayErrorBodyExpect {
    static let closed: GatewayErrorBody? = GatewayErrorBody.parse(#"{"detail":{"reason_code":"invalid_state","message":"This loop is already closed."}}"#)
}

@Test func errorBodiesReadBothGatewayShapes() {
    let plain = GatewayErrorBody.parse(#"{"detail":"Run 'x' not found"}"#)
    #expect(plain?.message == "Run 'x' not found" && plain?.reasonCode == nil)
    let env = GatewayErrorBody.parse(#"{"detail":{"reason_code":"invalid_state","message":"Already closed."}}"#)
    #expect(env?.reasonCode == "invalid_state" && env?.message == "Already closed.")
}

@Test func followStopsAtItsDeadlineWithTheAnswerStillComing() async throws {
    let mock = MockGatewayClient(answerDelay: .seconds(3600))
    let id = try await mock.startCapture(Capture(text: "x", occurredAt: .now, location: nil, idempotencyKey: "k", sessionId: "s"))
    let p = try await mock.follow({ try await mock.captureProgress(runId: id) }, every: .milliseconds(10), timeout: .milliseconds(80))
    #expect(p == .working)
}

@Test func theMockAnswersInTheDocumentedShapes() async throws {
    let mock = MockGatewayClient(answerDelay: .zero)
    let cap = try await mock.startCapture(Capture(text: "x", occurredAt: .now, location: nil, idempotencyKey: "k", sessionId: "s"))
    guard case .done(let a) = try await mock.captureProgress(runId: cap) else { Issue.record("capture"); return }
    #expect(a.response == MockGatewayClient.filingAnswer)
    let rec = try await mock.startRecall(question: "q", effort: .standard, sessionId: "s")
    guard case .done(let r) = try await mock.recallProgress(runId: rec) else { Issue.record("recall"); return }
    #expect(r.evidence.count == 2)
    try await mock.acknowledge(insightId: "i-1")
    #expect(try await mock.insights(limit: 5).isEmpty)
}
