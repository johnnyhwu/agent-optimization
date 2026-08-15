import React from "react";
import Banner from "./ui/Banner.jsx";
import Button from "./ui/Button.jsx";

// The last line of defence for a render-time exception.
//
// This exists because of a concrete failure: `RolloutDetail` handed `SpanList` an
// object where a string was expected, React threw "Objects are not valid as a
// React child", and with nothing catching it React unmounted the entire tree.
// The developer's screen went white — no message, no navigation, no way back
// except a reload, and nothing on the page naming the component at fault.
//
// A white page is the worst possible report of a bug, because it looks identical
// to a dead server, a broken build and a crashed browser tab. Anything at all is
// better, so this renders the error, says which section it came from, and offers
// the two recoveries that actually work: re-render this section, or go home.
//
// Deliberately a class: `componentDidCatch` has no hook equivalent, and React
// has never offered one.
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // The console is where a developer will look next, and React's own overlay
    // is not present in a production build. The component stack is the useful
    // half — the message alone rarely names the file.
    console.error("Unhandled render error", error, info?.componentStack);
  }

  // Remounting the subtree is a real recovery when the bad state came from a
  // route or a selection rather than from the data itself: the crash above was
  // triggered by *which* question was clicked, and clearing the error let the
  // page come back as soon as the selection changed.
  componentDidUpdate(prevProps) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <Banner
        tone="error"
        title={`Something in ${this.props.where || "this page"} failed to render`}
        actions={
          <Button variant="secondary" onClick={() => this.setState({ error: null })}>
            Try again
          </Button>
        }
      >
        This is a bug in the app, not something you did — the rest of the page is
        still there, and reloading will bring this section back.
        <div className="ui-banner-note">
          <code>{String(error?.message || error)}</code>
        </div>
      </Banner>
    );
  }
}
