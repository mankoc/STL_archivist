// A page that fails to render shows what went wrong (and a way back) instead of
// an empty window: without a boundary React unmounts the whole tree.
import { Component, type ReactNode } from "react";

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div style={{ padding: 24, fontFamily: "system-ui, sans-serif", color: "#c92a2a", background: "#fff", minHeight: "100vh" }}>
        <h2 style={{ marginTop: 0 }}>Something went wrong on this page</h2>
        <pre style={{ whiteSpace: "pre-wrap", color: "#333", fontSize: 13 }} data-testid="page-error">
          {String(error.message)}
          {"\n\n"}
          {error.stack}
        </pre>
        <button type="button" onClick={() => location.reload()}>Reload</button>{" "}
        <button type="button" onClick={() => this.setState({ error: null })}>Try to go on</button>
      </div>
    );
  }
}
