/**
 * Shown when the engine cannot be reached.
 *
 * Deliberately blank of content rather than filled with demonstration
 * data. Every screen that uses this could fall back to the mock generator
 * and look healthy; that would make "the engine is down" indistinguishable
 * from "the engine says everything is fine", which is the one confusion
 * this whole system is built to prevent.
 */
export default function EngineDown({ title, message }: { title: string; message: string }) {
  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold text-fg">{title}</h1>
      <div className="mt-4 rounded border border-danger/40 bg-danger/10 p-4 text-sm">
        <p className="font-medium text-danger">The analytical engine is not reachable.</p>
        <p className="mt-2 text-muted">{message}</p>
        <p className="mt-3 text-muted">Start it with:</p>
        <pre className="mt-2 overflow-x-auto rounded bg-bg p-2 text-xs">
          cd apps/api{'\n'}
          FDE_ALLOW_UNAUTHENTICATED=1 uvicorn fde_api.api.main:app --port 8000
        </pre>
        <p className="mt-3 text-muted">
          No demonstration data is shown here. This screen is empty rather than misleading.
        </p>
      </div>
    </div>
  );
}
