import { useEffect } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { DemoBanner, FreshBadge, Mono, Pill, cn } from '@fde/ui';
import { worstFreshness } from '@fde/calculations';
import { useDataset } from '../lib/api';
import { useStore } from '../lib/store';
import { useAuth } from '../lib/auth';
import ErrorBoundary from './ErrorBoundary';
import { fmtAgo, fmtMoney, fmtPct } from '../lib/format';

const NAV = (firstGameId: string | undefined) => [
  { to: '/', label: "Today's Picks", icon: '★' },
  { to: '/live', label: 'Live Slate', icon: '◉' },
  { to: '/slate', label: 'Weekly Slate', icon: '▤' },
  { to: firstGameId ? `/game/${firstGameId}` : '/slate', label: 'Game Lab', icon: '⚗' },
  { to: '/injuries', label: 'Injury Center', icon: '✚' },
  { to: '/market', label: 'Market Monitor', icon: '≋' },
  { to: '/portfolio', label: 'Bet Portfolio', icon: '▦' },
  { to: '/performance', label: 'Performance Lab', icon: '∿' },
  { to: '/models', label: 'Model Audit', icon: '⌘' },
  { to: '/health', label: 'Data Health', icon: '♥' },
  { to: '/settings', label: 'Settings', icon: '⚙' },
];

export default function Shell() {
  const { data: ds } = useDataset();
  const store = useStore();
  const { user, signOut } = useAuth();
  const location = useLocation();

  // Seed the append-only ledger from the demo dataset exactly once.
  useEffect(() => {
    if (ds) store.seedLedger(ds);
  }, [ds, store]);

  const overallHealth = ds ? worstFreshness(ds.feedStatuses.map((f) => f.status)) : 'MISSING';
  const bankroll = store.ledger?.bankrollBalance;

  return (
    <div className="flex min-h-screen bg-bg">
      {/* Left navigation */}
      {/* Collapses to an icon rail below lg so dense tables keep their width. */}
      <nav aria-label="Primary" className="sticky top-0 flex h-screen w-14 shrink-0 flex-col border-r border-edge bg-panel lg:w-52">
        <div className="border-b border-edge px-2 py-3 lg:px-4">
          <div className="text-sm font-bold tracking-wide text-ink">
            <span className="lg:hidden" aria-hidden="true">4<span className="text-accent">E</span></span>
            <span className="hidden lg:inline">FOURTH DOWN <span className="text-accent">EDGE</span></span>
          </div>
          <div className="mt-0.5 hidden text-[10px] uppercase tracking-widest text-ink-faint lg:block">Research Terminal</div>
        </div>
        <ul className="flex-1 overflow-y-auto py-2">
          {NAV(ds?.games[0]?.id).map((item) => (
            <li key={item.label}>
              <NavLink
                to={item.to}
                end={item.to === '/'}
                title={item.label}
                className={({ isActive }) =>
                  cn(
                    'mx-2 my-0.5 flex items-center justify-center gap-2.5 rounded px-2.5 py-1.5 text-[13px] lg:justify-start',
                    isActive
                      ? 'bg-accent/10 font-semibold text-accent'
                      : 'text-ink-muted hover:bg-panel-raised hover:text-ink',
                  )
                }
              >
                <span aria-hidden="true" className="w-4 text-center">{item.icon}</span>
                {/* Label stays in the accessibility tree when the rail collapses. */}
                <span className="sr-only lg:not-sr-only">{item.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="hidden border-t border-edge px-4 py-3 text-[11px] text-ink-faint lg:block">
          <p className="mb-1">Decision support only.</p>
          <p>No wagers are placed by this software.</p>
        </div>
      </nav>

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 border-b border-edge bg-panel/95 backdrop-blur">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 px-4 py-2">
            <div className="text-xs text-ink-muted">
              Season <Mono className="text-ink">{ds?.season ?? '—'}</Mono> · Week{' '}
              <Mono className="text-ink">{ds?.week ?? '—'}</Mono>{' '}
              <span className="text-warn">(DEMO)</span>
            </div>
            <div className="text-xs text-ink-muted">
              Last refresh:{' '}
              <Mono className="text-ink" title={ds ? ds.demoNow : undefined}>
                {ds ? fmtAgo(ds.demoNow, ds.demoNow) : '—'}
              </Mono>{' '}
              <span className="text-ink-faint">(frozen demo clock)</span>
            </div>
            <div className="flex items-center gap-1.5 text-xs text-ink-muted">
              Data health: <FreshBadge status={overallHealth} />
            </div>
            <Pill tone={store.settings.mode === 'PAPER' ? 'accent' : 'warn'}>
              {store.settings.mode === 'PAPER' ? 'PAPER MODE' : 'REAL TRACKING MODE'}
            </Pill>
            <div className="text-xs text-ink-muted">
              Bankroll: <Mono className="text-ink">{bankroll !== undefined ? fmtMoney(bankroll) : '—'}</Mono>
            </div>
            <div className="text-xs text-ink-muted">
              Open exposure:{' '}
              <Mono className="text-ink">
                {fmtMoney(store.openStake)} ({fmtPct(store.weeklyExposurePct)})
              </Mono>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span className="text-xs text-ink-faint">{user?.email}</span>
              <button
                onClick={() => void signOut()}
                className="rounded border border-edge px-2 py-1 text-[11px] text-ink-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
              >
                Sign out
              </button>
            </div>
          </div>
          <div className="px-4 pb-2">
            <DemoBanner label={ds?.label ?? 'DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS'} />
          </div>
        </header>

        <main className="min-w-0 flex-1 px-4 py-4">
          {/* Reset on navigation so a failed screen doesn't poison the next. */}
          <ErrorBoundary resetKey={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>

        <footer className="border-t border-edge px-4 py-2 text-[11px] text-ink-faint">
          Fourth Down Edge is a private research tool. It does not place wagers, does not guarantee outcomes, and its
          demo models are not validated for real-money decisions. All timestamps stored in UTC.
        </footer>
      </div>
    </div>
  );
}
