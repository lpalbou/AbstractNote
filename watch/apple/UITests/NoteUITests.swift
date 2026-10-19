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
