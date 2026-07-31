import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button, Card, CardHeader } from '@fde/ui';

interface Props {
  children: ReactNode;
  /** Changing this value resets the boundary (e.g. on route change). */
  resetKey?: string;
}

interface State {
  error: Error | null;
}

/**
 * Catches render errors so a failure on one screen degrades to a readable
 * error state instead of blanking the terminal. Analytical screens must fail
 * visibly and explain themselves — a blank panel could be mistaken for
 * "no opportunities found", which is a materially different message.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // No telemetry endpoint in v1; surface to the console for local diagnosis.
    console.error('Screen render failed:', error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <Card className="border-bad/40">
        <CardHeader
          title={<span className="text-bad">Screen failed to render</span>}
          hint="No prediction, price, or recommendation should be read from this screen while it is in this state."
        />
        <div className="space-y-3 p-4">
          <p className="text-xs text-ink-muted">
            This is an application fault, not a data condition. Do not interpret the missing content as
            &ldquo;no opportunities found&rdquo;.
          </p>
          <pre className="max-h-40 overflow-auto rounded border border-edge bg-bg p-2 font-mono text-[11px] text-bad">
            {error.message}
          </pre>
          <div className="flex gap-2">
            <Button onClick={() => this.setState({ error: null })}>Retry render</Button>
            <Button variant="ghost" onClick={() => window.location.reload()}>Reload application</Button>
          </div>
        </div>
      </Card>
    );
  }
}
