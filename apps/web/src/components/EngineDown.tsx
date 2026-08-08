import { Link } from 'react-router-dom';

/**
 * Shown when the engine cannot be reached.
 *
 * Deliberately blank of content rather than filled with demonstration
 * data. Every screen that uses this could fall back to the mock generator
 * and look healthy; that would make "the engine is down" indistinguishable
 * from "the engine says everything is fine", which is the one confusion
 * this whole system is built to prevent.
 *
 * That refusal is not the same as leaving the reader stranded, and the
 * difference started to matter when `/` began redirecting here: this is
 * now the FRONT DOOR when the engine is off, so a screen with no way out
 * of it is the first thing a person sees. The demonstration screens are
 * offered by name, as demonstration screens, and they carry their own
 * banner when reached. Naming them is not falling back to them - the
 * reader chooses, knowing which is which, which is exactly the property
 * a silent fallback destroys.
 */
export default function EngineDown({
  title,
  message,
  expectedAt,
}: {
  title: string;
  message: string;
  expectedAt?: string;
}) {
  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold text-fg">{title}</h1>
      <div className="mt-4 rounded border border-danger/40 bg-danger/10 p-4 text-sm">
        <p className="font-medium text-danger">The analytical engine is not reachable.</p>
        <p className="mt-2 text-muted">{message}</p>
        <p className="mt-3 text-muted">
          {expectedAt ? (
            <>
              Expected at <code>{expectedAt}</code>. Start it with:
            </>
          ) : (
            'Start it with:'
          )}
        </p>
        <pre className="mt-2 overflow-x-auto rounded bg-bg p-2 text-xs">
          cd apps/api{'\n'}
          FDE_ALLOW_UNAUTHENTICATED=1 uvicorn fde_api.api.main:app --port 8000
        </pre>
        <p className="mt-3 text-muted">
          No demonstration data is shown here. This screen is empty rather than misleading.
        </p>
      </div>
      <p className="mt-4 text-sm text-muted">
        Nothing on the engine-backed screens will work until it is running. The{' '}
        <Link to="/picks-demo" className="text-accent underline">
          demonstration screens
        </Link>{' '}
        run without it — they are generated data, labelled as such on every page.
      </p>
    </div>
  );
}
