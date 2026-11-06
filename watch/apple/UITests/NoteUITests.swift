import XCTest

/// Real taps against the sample notebook (`--mock`). Run on their own scheme:
/// `xcodebuild test -scheme AbstractNoteWatchUITests -destination 'platform=watchOS Simulator,id=…'`.
final class NoteUITests: XCTestCase {
    override func setUp() { continueAfterFailure = false }

    private func launch(_ screen: String) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--mock", "--screen", screen]
        app.launch()
        return app
    }

    /// A sent note answers with how it was filed, and Done starts a new one.
    @MainActor func testASentNoteShowsItsFilingAnswerThenDoneStartsANewNote() {
        let app = launch("note-saved")
        let answer = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Saved as'")).firstMatch
        XCTAssertTrue(answer.waitForExistence(timeout: 10))
        app.buttons["note.done"].tap()
        XCTAssertTrue(app.buttons["note.send"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["note.send"].isEnabled, "Send waits for words")
    }

    /// A failed send says so in one sentence and offers the same note again.
    @MainActor func testAFailedSendOffersTryAgain() {
        let app = launch("note-error")
        XCTAssertTrue(app.buttons["Try again"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Can't reach notes.example.org."].waitForExistence(timeout: 5))
    }

    @MainActor func testCheckingServerKeepsTheFailedNoteAndItsRetry() {
        let app = launch("note-error")
        let check = app.buttons["note.checkServer"]
        XCTAssertTrue(check.waitForExistence(timeout: 10))
        for _ in 0..<5 where !check.isHittable { app.swipeUp() }
        check.tap()
        XCTAssertTrue(app.textFields["server.address"].waitForExistence(timeout: 5))
        app.navigationBars.buttons.firstMatch.tap()
        XCTAssertTrue(app.buttons["Try again"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Met Dana about the LUT budget'")).firstMatch.exists)
        app.buttons["Edit"].tap()
        XCTAssertTrue(app.buttons["note.send"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["note.send"].isEnabled, "Checking the server must not discard the note's words")
    }

    @MainActor func testChangingServerKeepsNewPortAfterSignOutAndRelaunch() {
        let app = launch("server")
        let field = app.textFields["server.address"]
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        let old = "http://192.0.2.1:18080", replacement = "http://192.0.2.1:8080"
        XCTAssertEqual(field.value as? String, old)
        field.tap()
        app.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: old.count) + replacement)
        if app.buttons["Done"].firstMatch.exists { app.buttons["Done"].firstMatch.tap() }
        let save = app.buttons["server.save"]
        for _ in 0..<5 where !save.isHittable { app.swipeUp() }
        save.tap()
        let server = app.buttons["signin.server"]
        XCTAssertTrue(server.waitForExistence(timeout: 10))
        XCTAssertEqual(server.label, "Server " + replacement)
        XCTAssertTrue(app.staticTexts["Sign in to \(replacement)."].exists)
        app.terminate()
        app.launchArguments = ["--mock", "--screen", "signin"]
        app.launch()
        XCTAssertTrue(server.waitForExistence(timeout: 10))
        XCTAssertEqual(server.label, "Server " + replacement)
    }

    /// Closing an open loop takes a tap and removes it from Today.
    @MainActor func testCloseRemovesTheLoopFromToday() {
        let app = launch("today")
        let close = app.buttons["loop.close.t-7"]
        XCTAssertTrue(close.waitForExistence(timeout: 10))
        close.tap()
        XCTAssertTrue(app.buttons["loop.close.t-8"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["loop.close.t-7"].exists)
    }

    /// Quick is the watch's default: the matching notes, at once.
    @MainActor func testQuickAskListsTheMatchingNotes() {
        let app = launch("ask-quick")
        let answer = app.staticTexts.matching(NSPredicate(format: "label ENDSWITH 'notes match.'")).firstMatch
        XCTAssertTrue(answer.waitForExistence(timeout: 10))
    }
}
