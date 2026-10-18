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
        XCTAssertTrue(app.staticTexts["No connection."].exists || app.otherElements["note.error"].exists)
    }

    /// Acknowledging an insight takes a tap and removes it from Today.
    @MainActor func testGotItRemovesTheInsightFromToday() {
        let app = launch("insight")
        let ack = app.buttons["insight.ack"]
        XCTAssertTrue(ack.waitForExistence(timeout: 10))
        ack.tap()
        XCTAssertTrue(app.staticTexts["Open loops"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["The LUT budget changed twice this month"].exists)
    }
}
