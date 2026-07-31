import { useMemo, useState } from 'react';
import { Button, Card, CardHeader, EmptyState, ErrorState, LoadingState, Mono, Pill, Stat, Td, Th } from '@fde/ui';
import { maxDrawdown } from '@fde/calculations';
import type { Bet } from '@fde/shared-types';
import { useDataset } from '../lib/api';
import { useStore } from '../lib/store';
import { fmtMarketLine, fmtMoney, fmtOdds, fmtPct, fmtSignedMoney, fmtUtc } from '../lib/format';
import { gameById, gameLabel, teamById } from '../lib/joins';

type Tab = 'OPEN' | 'SETTLED' | 'ALL';

export default function BetPortfolio() {
  const { data: ds, isLoading, error } = useDataset();
  const store = useStore();
  const [tab, setTab] = useState<Tab>('OPEN');
  const [settleTarget, setSettleTarget] = useState<string | null>(null);
  const [settleResult, setSettleResult] = useState<'WIN' | 'LOSS' | 'PUSH' | 'VOID'>('WIN');
  const [closingAmerican, setClosingAmerican] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);

  const bets = store.ledger?.bets ?? [];
  const settled = useMemo(() => bets.filter((b) => b.result !== 'PENDING'), [bets]);
  const open = useMemo(() => bets.filter((b) => b.result === 'PENDING'), [bets]);
  const shown = tab === 'OPEN' ? open : tab === 'SETTLED' ? settled : bets;

  const realized = settled.reduce((a, b) => {
    if (b.result === 'WIN') return a + ((b.payout ?? 0) - b.stake);
    if (b.result === 'LOSS') return a - b.stake;
    return a;
  }, 0);
  const clvBets = settled.filter((b) => b.closingLineValuePct !== undefined);
  const avgClv = clvBets.length ? clvBets.reduce((a, b) => a + (b.closingLineValuePct ?? 0), 0) / clvBets.length : 0;

  const bankrollCurve = useMemo(() => {
    let bal = 10_000;
    const curve = [bal];
    for (const b of [...settled].sort((x, y) => (x.settledAt ?? '').localeCompare(y.settledAt ?? ''))) {
      if (b.result === 'WIN') bal += (b.payout ?? 0) - b.stake;
      else if (b.result === 'LOSS') bal -= b.stake;
      curve.push(bal);
    }
    return curve;
  }, [settled]);

  const teamExposure = useMemo(() => {
    const m = new Map<string, number>();
    if (!ds) return m;
    for (const b of open) {
      const g = gameById(ds, b.gameId);
      if (!g) continue;
      const teamId = b.selection === 'HOME' ? g.homeTeamId : b.selection === 'AWAY' ? g.awayTeamId : null;
      if (teamId) m.set(teamId, (m.get(teamId) ?? 0) + b.stake);
    }
    return m;
  }, [open, ds]);

  if (isLoading) return <LoadingState label="Loading portfolio…" />;
  if (error || !ds || !store.ledger) return <ErrorState title="Failed to load portfolio" />;

  function doSettle(bet: Bet) {
    setActionError(null);
    const closing = closingAmerican === '' ? undefined : Number(closingAmerican);
    const res = store.settle({
      betId: bet.id,
      result: settleResult,
      settledAt: ds!.demoNow,
      closingAmerican: closing,
      closingLine: bet.line,
      closingLineValuePct: undefined,
      actor: store.settings.userId,
    });
    if (res.error) setActionError(res.error);
    else {
      setSettleTarget(null);
      setClosingAmerican('');
    }
  }

  const resultPill = (r: Bet['result']) =>
    r === 'PENDING' ? <Pill tone="accent">OPEN</Pill>
      : r === 'WIN' ? <Pill tone="ok">WIN</Pill>
        : r === 'LOSS' ? <Pill tone="bad">LOSS</Pill>
          : r === 'PUSH' ? <Pill tone="neutral">PUSH</Pill>
            : <Pill tone="warn">VOID</Pill>;

  return (
    <div className="space-y-4">
      <section aria-label="Portfolio metrics" className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Current bankroll" value={fmtMoney(store.ledger.bankrollBalance)} />
        <Stat label="Available" value={fmtMoney(store.ledger.bankrollBalance - store.openStake)} sub="bankroll − open stake" />
        <Stat label="Open stake" value={fmtMoney(store.openStake)} sub={`${open.length} open bets`} />
        <Stat label="Weekly exposure" value={fmtPct(store.weeklyExposurePct)} sub={`cap ${fmtPct(store.settings.riskControls.maxWeeklyOpenStakePct)}`} />
        <Stat label="Max weekly loss" value={fmtMoney(store.openStake)} sub="if every open bet loses" tone="warning" />
        <Stat label="Realized P/L" value={fmtSignedMoney(realized)} tone={realized >= 0 ? 'positive' : 'danger'} sub="paper, demo data" />
        <Stat label="Avg CLV" value={`${avgClv >= 0 ? '+' : ''}${avgClv.toFixed(1)} pts`} sub={`${clvBets.length} bets · drawdown ${fmtPct(maxDrawdown(bankrollCurve))}`} />
      </section>

      <Card>
        <CardHeader
          title="Bet ledger — append-only"
          hint="Settled history is never silently overwritten; corrections append audit events"
          right={
            <div role="tablist" aria-label="Ledger view" className="flex gap-1">
              {(['OPEN', 'SETTLED', 'ALL'] as Tab[]).map((t) => (
                <button
                  key={t}
                  role="tab"
                  aria-selected={tab === t}
                  onClick={() => setTab(t)}
                  className={`rounded px-2.5 py-1 text-[11px] font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${
                    tab === t ? 'bg-accent/15 text-accent' : 'text-ink-muted hover:text-ink'
                  }`}
                >
                  {t} ({t === 'OPEN' ? open.length : t === 'SETTLED' ? settled.length : bets.length})
                </button>
              ))}
            </div>
          }
        />
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <caption className="sr-only">Paper bet ledger</caption>
            <thead>
              <tr>
                <Th>Placed @ (UTC)</Th><Th>Game</Th><Th>Market</Th><Th>Selection</Th><Th>Line</Th>
                <Th>Price</Th><Th>Stake</Th><Th>Mode</Th><Th>Result</Th><Th>P/L</Th>
                <Th>Close</Th><Th>CLV</Th><Th>Model</Th><Th>Links</Th>
                <Th className="sticky right-0 z-20 border-l border-edge bg-panel-raised">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {shown.length === 0 ? (
                <tr><Td colSpan={15}><EmptyState title={`No ${tab.toLowerCase()} bets`} hint="Record paper bets from qualifying BET cards." /></Td></tr>
              ) : (
                shown.map((b) => {
                  const pl = b.result === 'WIN' ? (b.payout ?? 0) - b.stake : b.result === 'LOSS' ? -b.stake : 0;
                  return (
                    <tr key={b.id} className="hover:bg-panel-raised/60">
                      <Td><Mono className="text-ink-faint">{fmtUtc(b.placedAt)}</Mono></Td>
                      <Td className="font-medium">{gameLabel(ds, b.gameId)}</Td>
                      <Td>{b.market}</Td>
                      <Td>{b.selection}</Td>
                      <Td><Mono>{fmtMarketLine(b.market, b.line)}</Mono></Td>
                      <Td><Mono>{fmtOdds(b.american, store.settings.oddsFormat)}</Mono></Td>
                      <Td><Mono>{fmtMoney(b.stake)}</Mono></Td>
                      <Td><Pill tone={b.mode === 'PAPER' ? 'accent' : 'warn'}>{b.mode}</Pill></Td>
                      <Td>{resultPill(b.result)}</Td>
                      <Td><Mono className={pl > 0 ? 'text-ok' : pl < 0 ? 'text-bad' : 'text-ink-faint'}>{b.result === 'PENDING' ? '—' : fmtSignedMoney(pl)}</Mono></Td>
                      <Td><Mono className="text-ink-faint">{b.closingAmerican !== undefined ? `${fmtMarketLine(b.market, b.closingLine)} @ ${fmtOdds(b.closingAmerican, store.settings.oddsFormat)}` : '—'}</Mono></Td>
                      <Td><Mono className={b.closingLineValuePct !== undefined && b.closingLineValuePct > 0 ? 'text-ok' : 'text-ink-muted'}>{b.closingLineValuePct !== undefined ? `${b.closingLineValuePct > 0 ? '+' : ''}${b.closingLineValuePct.toFixed(1)}` : '—'}</Mono></Td>
                      <Td><Mono className="text-model">{b.modelVersionId ?? '—'}</Mono></Td>
                      <Td className="text-[10px] text-ink-faint">
                        {(() => {
                          const linkSummary = `recommendation ${b.recommendationId ?? 'none'}, prediction ${b.predictionId ?? 'none'}, feature snapshot ${b.featureSnapshotId ?? 'none'}`;
                          return (
                            <span
                              tabIndex={0}
                              title={`recommendation: ${b.recommendationId ?? '—'}\nprediction: ${b.predictionId ?? '—'}\nfeature snapshot: ${b.featureSnapshotId ?? '—'}`}
                              aria-label={linkSummary}
                              className="cursor-help underline decoration-dotted underline-offset-2 focus:outline focus:outline-2 focus:outline-accent"
                            >
                              rec·pred·snap
                            </span>
                          );
                        })()}
                      </Td>
                      <Td className="sticky right-0 z-10 border-l border-edge bg-panel">
                        {b.result === 'PENDING' ? (
                          settleTarget === b.id ? (
                            <div className="flex items-center gap-1.5">
                              <select
                                value={settleResult}
                                onChange={(e) => setSettleResult(e.target.value as typeof settleResult)}
                                aria-label="Settlement result"
                                className="rounded border border-edge bg-panel-raised px-1 py-0.5 text-[11px]"
                              >
                                <option>WIN</option><option>LOSS</option><option>PUSH</option><option>VOID</option>
                              </select>
                              <input
                                value={closingAmerican}
                                onChange={(e) => setClosingAmerican(e.target.value)}
                                placeholder="close"
                                aria-label="Closing American odds"
                                className="w-14 rounded border border-edge bg-panel-raised px-1 py-0.5 font-mono text-[11px]"
                              />
                              <Button className="px-2 py-0.5 text-[11px]" onClick={() => doSettle(b)}>OK</Button>
                              <Button variant="ghost" className="px-1.5 py-0.5 text-[11px]" onClick={() => setSettleTarget(null)}>✕</Button>
                            </div>
                          ) : (
                            <Button className="px-2 py-0.5 text-[11px]" onClick={() => { setSettleTarget(b.id); setActionError(null); }}>
                              Settle
                            </Button>
                          )
                        ) : (
                          <span className="text-[10px] text-ink-faint" title="Settled records are immutable; corrections append audit events.">immutable</span>
                        )}
                      </Td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        {actionError ? <p role="alert" className="px-3 py-2 text-xs text-bad">{actionError}</p> : null}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Exposure by team" hint="Weekly per-team cap 1.50% of bankroll" />
          {teamExposure.size === 0 ? (
            <div className="p-3"><EmptyState title="No open team exposure" /></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead><tr><Th>Team</Th><Th>Open stake</Th><Th>% of bankroll</Th><Th>Cap</Th></tr></thead>
                <tbody>
                  {[...teamExposure.entries()].map(([teamId, stake]) => {
                    const pct = stake / store.ledger!.bankrollBalance;
                    const cap = store.settings.riskControls.maxPerTeamWeeklyPct;
                    return (
                      <tr key={teamId}>
                        <Td>{teamById(ds, teamId).name}</Td>
                        <Td><Mono>{fmtMoney(stake)}</Mono></Td>
                        <Td><Mono className={pct > cap ? 'text-bad' : 'text-ink'}>{fmtPct(pct, 2)}</Mono></Td>
                        <Td><Mono className="text-ink-faint">{fmtPct(cap, 2)}</Mono></Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title="Bet events & audit trail" hint="Every placement, settlement, and correction is recorded" />
          <div className="max-h-72 overflow-y-auto">
            <table className="w-full border-collapse">
              <thead><tr><Th>At (UTC)</Th><Th>Type</Th><Th>Detail</Th><Th>Actor</Th></tr></thead>
              <tbody>
                {[...(store.ledger.betEvents ?? [])].reverse().map((e) => (
                  <tr key={e.id}>
                    <Td><Mono className="text-ink-faint">{fmtUtc(e.createdAt)}</Mono></Td>
                    <Td>
                      <Mono className={e.eventType === 'CORRECTED' ? 'text-warn' : 'text-ink-muted'}>{e.eventType}</Mono>
                    </Td>
                    <Td className="max-w-96 whitespace-normal text-[11px] text-ink-muted">{e.detail}</Td>
                    <Td className="text-ink-faint">{e.actor}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
