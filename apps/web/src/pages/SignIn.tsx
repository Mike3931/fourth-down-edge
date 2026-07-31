import { useState, type FormEvent } from 'react';
import { Button, Card, DemoBanner } from '@fde/ui';
import { DEMO_DATA_LABEL } from '@fde/shared-types';
import { useAuth } from '../lib/auth';

export default function SignIn() {
  const { supabaseConfigured, signInWithEmail, signInLocalDemo } = useAuth();
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setStatus(null);
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      setError('Enter a valid email address.');
      return;
    }
    const res = await signInWithEmail(email);
    if (res.error) setError(res.error);
    else setStatus('Check your email for a sign-in link.');
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="w-full max-w-md">
        <div className="mb-4 text-center">
          <h1 className="text-xl font-bold tracking-wide text-ink">
            FOURTH DOWN <span className="text-accent">EDGE</span>
          </h1>
          <p className="mt-1 text-xs uppercase tracking-widest text-ink-faint">
            NFL probability &amp; pricing research terminal
          </p>
        </div>
        <Card className="p-5">
          <form onSubmit={(e) => void onSubmit(e)} noValidate>
            <label htmlFor="email" className="mb-1 block text-xs font-medium text-ink-muted">
              Email address
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mb-3 w-full rounded border border-edge bg-bg px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
              placeholder="analyst@example.com"
              aria-invalid={!!error}
              aria-describedby={error ? 'email-error' : undefined}
            />
            {error ? (
              <p id="email-error" role="alert" className="mb-3 text-xs text-bad">
                {error}
              </p>
            ) : null}
            {status ? <p className="mb-3 text-xs text-ok">{status}</p> : null}
            <Button type="submit" variant="primary" className="w-full" disabled={!supabaseConfigured}>
              {supabaseConfigured ? 'Send sign-in link' : 'Supabase not configured'}
            </Button>
          </form>
          <div className="my-4 flex items-center gap-3 text-[11px] text-ink-faint">
            <span className="h-px flex-1 bg-edge" />
            or
            <span className="h-px flex-1 bg-edge" />
          </div>
          <Button onClick={signInLocalDemo} className="w-full">
            Enter local demo session
          </Button>
          <p className="mt-3 text-[11px] leading-relaxed text-ink-faint">
            The local demo session stores everything on this device only. Paper betting is the default mode.
            This software never stores sportsbook credentials and never places wagers.
          </p>
        </Card>
        <div className="mt-4">
          <DemoBanner label={DEMO_DATA_LABEL} />
        </div>
      </div>
    </div>
  );
}
