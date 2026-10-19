import { Component, type ReactNode } from "react";

/** One view that cannot render its data never blanks the whole app. */
export class ViewBoundary extends Component<{ children: ReactNode; name: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    console.error(`The ${this.props.name} view failed to render`, error);
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="nt-center">
        <div className="nt-error" role="alert">
          <span>This view could not be shown.</span>
          <button type="button" className="nt-btn nt-btn--quiet" onClick={() => this.setState({ failed: false })}>
            Retry
          </button>
        </div>
      </div>
    );
  }
}
