import { useEffect } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { DemoBanner, FreshBadge, Mono, Pill, cn } from '@fde/ui';
import { worstFreshness } from '@fde/calculations';
import { useDataset } from '../lib/api';
import { useStore } from '../lib/store';
import { useAuth } from '../lib/auth';
import ErrorBoundary from './ErrorBoundary';
import { fmtAgo, fmtMoney, fmtPct } from '../lib/format';

// `live` marks a screen backed by the analytical engine. The rest read the
// demonstration generator, and the nav says which is which — mixing real
// and generated data behind identical labels is the one thing that makes
// the whole terminal untrustworthy.
//
// `group` is the heading a screen sits under, and it is NOT the same
// question as `live`. Settings reads no dataset at all: it holds paper
// mode, the hard monthly loss budget, and the bankroll caps, every one of
// which governs real behaviour. Filing it under "Demonstration data"
// because it is not engine-backed told the reader those controls were
// part of the demo. They are the most consequential controls in the app.
const LIVE_GROUP = 'Live engine data';
const DEMO_GROUP = 'Demonstration data';
const SETUP_GROUP = 'Your setup';

const NAV = (firstGameId: string | undefined) => [
  { to: '/live', label: 'Live Slate', icon: '◉', live: true, group: LIVE_GROUP },
  { to: '/slate', label: 'Slate', icon: '▤', live: true, group: LIVE_GROUP },
  { to: '/models', label: 'Model Audit', icon: '⌘', live: true, group: LIVE_GROUP },
  { to: '/health', label: 'Data Health', icon: '♥', live: true, group: LIVE_GROUP },
  { to: '/picks-demo', label: "Today's Picks", icon: '★', live: false, group: DEMO_GROUP },
  // Falls back to the DEMO slate, not the live one. A nav item sitting
  // under "Demonstration data" that lands the reader on engine-backed
  // data is the exact confusion this split exists to prevent.
  { to: firstGameId ? `/game/${firstGameId}` : '/slate-demo', label: 'Game Lab', icon: '⚗', live: false, group: DEMO_GROUP },
  { to: '/injuries', label: 'Injury Center', icon: '✚', live: false, group: DEMO_GROUP },
  { to: '/market', label: 'Market Monitor', icon: '≋', live: false, group: DEMO_GROUP },
  { to: '/portfolio', label: 'Bet Portfolio', icon: '▦', live: false, group: DEMO_GROUP },
  { to: '/performance', label: 'Performance Lab', icon: '∿', live: false, group: DEMO_GROUP },
  { to: '/settings', label: 'Settings', icon: '⚙', live: false, group: SETUP_GROUP },
];

// The four engine-backed routes. Kept as an explicit list rather than
// derived from NAV, because NAV holds one dynamic path (`/game/:id`) and a
// prefix match over it would quietly reclassify screens as the router
// grows. A screen that is wrongly called live is the failure that matters,
// so the list is stated rather than inferred.
const LIVE_ROUTES = new Set(['/live', '/slate', '/models', '/health']);

// Screens that read NO dataset — neither the engine nor the demo
// generator. Settings is the whole list: it holds paper mode, the hard
// monthly loss budget and the bankroll caps, and it reads none of them
// from a dataset.
//
// The nav already stopped filing these under "Demonstration data",
// because saying so told the reader those controls were part of the
// demo. The header was still stamping DEMONSTRATION DATA — NOT FOR
// REAL-MONEY DECISIONS directly above them, which is the same claim by
// another route. A banner about the data on screen does not belong on a
// screen that shows no data.
const SETUP_ROUTES = new Set(['/settings']);

function isLiveRoute(pathname: string): boolean {
  return LIVE_ROUTES.has(pathname);
}

function isSetupRoute(pathname: string): boolean {
  return SETUP_ROUTES.has(pathname);
}

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
  const onLiveScreen = isLiveRoute(location.pathname);
  const onSetupScreen = isSetupRoute(location.pathname);
  // Demo chrome belongs only to screens that actually show demo data.
  const onDemoScreen = !onLiveScreen && !onSetupScreen;

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
          {NAV(ds?.games[0]?.id).map((item, i, all) => (
            <li key={item.label}>
              {/* One heading at each boundary, so the split is impossible
                  to miss without repeating a badge on every row. */}
              {(i === 0 || all[i - 1]?.group !== item.group) && (
                <div className="mt-2 mb-1 hidden px-4 text-[10px] uppercase tracking-widest text-ink-faint lg:block">
                  {item.group}
                </div>
              )}
              <NavLink
                to={item.to}
                end={item.to === '/'}
                // From the group, not from `live`. Deriving it from `live`
                // gave Settings the tooltip "Settings — demonstration
                // data", the same claim the heading above it had already
                // stopped making.
                title={`${item.label} — ${item.group.toLowerCase()}`}
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
                {item.live && (
                  <span
                    aria-hidden="true"
                    className="ml-auto hidden h-1.5 w-1.5 rounded-full bg-emerald-400 lg:block"
                  />
                )}
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
        {/* The header describes the DATA ON THE SCREEN BELOW IT. It used
            to describe the demo dataset unconditionally, so the live
            screens carried a frozen demo clock, a demo season and week, a
            demo bankroll, and a red banner reading DEMONSTRATION DATA —
            directly above real captured book prices. Chrome that
            contradicts its own content teaches the reader to ignore the
            chrome, which is the opposite of what these labels are for. */}
        <header className="sticky top-0 z-20 border-b border-edge bg-panel/95 backdrop-blur">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 px-4 py-2">
            {onSetupScreen ? (
              <div className="text-xs text-ink-muted">
                Your settings. These control real behaviour and are not part of
                the demonstration data.
              </div>
            ) : onLiveScreen ? (
              <div className="flex items-center gap-1.5 text-xs text-ink-muted">
                <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                Real captured data — no generated values on this screen. Capture
                time and provider are stated per item below.
              </div>
            ) : (
              <>
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
              </>
            )}
            {/* Paper mode is a real setting about real behaviour, so it is
                stated on every screen regardless of the data source. */}
            <Pill tone={store.settings.mode === 'PAPER' ? 'accent' : 'warn'}>
              {store.settings.mode === 'PAPER' ? 'PAPER MODE' : 'REAL TRACKING MODE'}
            </Pill>
            {onDemoScreen && (
              <>
                <div className="text-xs text-ink-muted">
                  Bankroll: <Mono className="text-ink">{bankroll !== undefined ? fmtMoney(bankroll) : '—'}</Mono>
                </div>
                <div className="text-xs text-ink-muted">
                  Open exposure:{' '}
                  <Mono className="text-ink">
                    {fmtMoney(store.openStake)} ({fmtPct(store.weeklyExposurePct)})
                  </Mono>
                </div>
              </>
            )}
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
          {onDemoScreen && (
            <div className="px-4 pb-2">
              <DemoBanner label={ds?.label ?? 'DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS'} />
            </div>
          )}
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
