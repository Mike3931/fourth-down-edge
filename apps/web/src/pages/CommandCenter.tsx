import { Link } from 'react-router-dom';
import { Card, CardHeader, EmptyState, ErrorState, LoadingState, Mono, RecBadge, Stat, Td, Th } from '@fde/ui';
import { brierScore, logLoss } from '@fde/calculations';
import { useDataset, useRecommendations } from '../lib/api';
import { useStore } from '../lib/store';
import { fmtAgo, fmtMarketLine, fmtMoney, fmtOdds, fmtPct, fmtSignedPct } from '../lib/format';
import { gameLabel, modelVersionLabel } from '../lib/joins';

export default function CommandCenter() {
  const { data: ds, isLoading, error } = useDataset();
  const { data: recs, isLoading: recsLoading } = useRecommendations();
  const store = useStore();

  if (isLoading || recsLoading) return <LoadingState label="Evaluating slate…" />;
  if (error || !ds || !recs) return <ErrorState title="Failed to load dataset" detail={String(error ?? 'unknown')} />;

  const counts = {
    BET: recs.filter((r) => r.status === 'BET').length,
    WATCH: recs.filter((r) => r.status === 'WATCH').length,
    PASS: recs.filter((r) => r.status === 'PASS').length,
    'DATA INCOMPLETE': recs.filter((r) => r.status === 'DATA INCOMPLETE').length,
  };
  const evaluated = recs.filter((r) => r.status === 'BET' || r.status === 'WATCH');
  const avgEdge = evaluated.length
    ? evaluated.reduce((a, r) => a + r.edge, 0) / evaluated.length
    : 0;
  const settled = (store.ledger?.bets ?? []).filter((b) => b.closingLineValuePct !== undefined);
  const avgClv = settled.length
    ? settled.reduce((a, b) => a + (b.closingLineValuePct ?? 0), 0) / settled.length
    : 0;
  const btPairs = ds.backtest.map((b) => ({ p: b.p, outcome: b.outcome }));
  const healthOk = ds.feedStatuses.filter((f) => f.status === 'CURRENT').length;
  const healthScore = healthOk / ds.feedStatuses.length;

  const best = recs
    .filter((r) => (r.status === 'BET' || r.status === 'WATCH') && r.edge > 0)
    .sort((a, b) => b.edge - a.edge)
    .slice(0, 6);

  // Priority queue derived from data conditions.
  const queue: Array<{ id: string; title: string; detail: string; tone: 'warn' | 'bad' | 'accent' }> = [];
  const staleFeed = ds.feedStatuses.find((f) => f.feed === 'odds' && f.status === 'STALE');
  if (staleFeed) {
    for (const gid of staleFeed.impactedGameIds) {
      queue.push({ id: `stale-${gid}`, title: 'Stale market data', detail: `${gameLabel(ds, gid)}: consensus odds snapshot exceeded freshness threshold. Recommendation downgraded.`, tone: 'bad' });
    }
  }
  const missingWx = ds.feedStatuses.find((f) => f.feed === 'weather' && f.status === 'MISSING');
  if (missingWx) {
    for (const gid of missingWx.impactedGameIds) {
      queue.push({ id: `wx-${gid}`, title: 'Weather forecast unavailable', detail: `${gameLabel(ds, gid)}: outdoor game with no usable forecast. DATA INCOMPLETE until restored.`, tone: 'bad' });
    }
  }
  for (const r of recs.filter((x) => x.status === 'DATA INCOMPLETE')) {
    if (r.statusReasons.some((s) => s.includes('quarterback'))) {
      queue.push({ id: `qb-${r.gameId}`, title: 'Starting quarterback uncertainty', detail: `${gameLabel(ds, r.gameId)}: QB active probability in the unresolved band. Await official designation.`, tone: 'warn' });
    }
  }
  const agingManual = ds.manualPrices.filter((m) => {
    const ageMin = (new Date(ds.demoNow).getTime() - new Date(m.enteredAt).getTime()) / 60_000;
    return m.confirmedVisible && ageMin > 45 && ageMin < 24 * 60;
  });
  for (const m of agingManual) {
    queue.push({ id: `reconfirm-${m.id}`, title: 'Price reconfirmation needed', detail: `${gameLabel(ds, m.gameId)}: manual ${m.sportsbook} price entered ${fmtAgo(m.enteredAt, ds.demoNow)} — reconfirm before acting.`, tone: 'warn' });
  }
  for (const r of ds.injuryReports.filter((x) => x.conflictWarning)) {
    queue.push({ id: `conflict-${r.id}`, title: 'Injury report changed / conflicting', detail: `${gameLabel(ds, r.gameId)}: ${r.conflictWarning}.`, tone: 'warn' });
  }
  if (store.weeklyExposurePct > store.settings.riskControls.maxWeeklyOpenStakePct * 0.75) {
    queue.push({ id: 'risk', title: 'Weekly risk limit approaching', detail: `Open stake is ${fmtPct(store.weeklyExposurePct)} of bankroll (limit ${fmtPct(store.settings.riskControls.maxWeeklyOpenStakePct)}).`, tone: 'warn' });
  }
  const wxSevere = ds.weatherSnapshots.filter((w) => w.severity === 'HIGH' || w.severity === 'CRITICAL');
  for (const w of wxSevere) {
    queue.push({ id: `wind-${w.gameId}`, title: 'Weather forecast changed', detail: `${gameLabel(ds, w.gameId)}: wind ${w.windMph} mph (gusts ${w.gustMph}). Totals uncertainty elevated.`, tone: 'accent' });
  }

  return (
    <div className="space-y-4">
      <h1 className="sr-only">Command Center</h1>

      {/* Summary metrics */}
      <section aria-label="Summary metrics" className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-6">
        <Stat label="Games on slate" value={ds.games.length} />
        <Stat label="BET" value={counts.BET} tone={counts.BET > 0 ? 'positive' : 'default'} />
        <Stat label="WATCH" value={counts.WATCH} tone="warning" />
        <Stat label="PASS" value={counts.PASS} />
        <Stat label="Data incomplete" value={counts['DATA INCOMPLETE']} tone={counts['DATA INCOMPLETE'] > 0 ? 'danger' : 'default'} />
        <Stat label="Bankroll" value={fmtMoney(store.ledger?.bankrollBalance ?? 0)} />
        <Stat label="Weekly exposure" value={fmtPct(store.weeklyExposurePct)} sub={`${fmtMoney(store.openStake)} open`} />
        <Stat label="Avg model edge" value={fmtSignedPct(avgEdge)} sub="BET + WATCH only" />
        <Stat label="Avg closing-line value" value={fmtSignedPct(avgClv / 100)} sub={`${settled.length} settled demo bets`} />
        <Stat
          label="Model version"
          value={<span className="text-model">{modelVersionLabel(ds, ds.predictions.find((p) => p.isOfficial)?.modelVersionId)}</span>}
          mono={false}
        />
        <Stat label="Data-health score" value={fmtPct(healthScore, 0)} tone={healthScore > 0.85 ? 'positive' : 'warning'} sub={`${healthOk}/${ds.feedStatuses.length} feeds current`} />
        <Stat label="Backtest log loss / Brier" value={`${logLoss(btPairs).toFixed(3)} / ${brierScore(btPairs).toFixed(3)}`} sub="synthetic demo backtest" />
      </section>

      {/* Priority queue */}
      <Card>
        <CardHeader title="Priority queue" hint="Conditions requiring analyst attention, derived from live data checks" />
        <div className="grid grid-cols-1 gap-2 p-3 md:grid-cols-2 xl:grid-cols-3">
          {queue.length === 0 ? (
            <EmptyState title="No outstanding attention items" />
          ) : (
            queue.map((q) => (
              <div
                key={q.id}
                className={`rounded border px-3 py-2 ${
                  q.tone === 'bad' ? 'border-bad/40 bg-bad/5' : q.tone === 'warn' ? 'border-warn/40 bg-warn/5' : 'border-accent/40 bg-accent/5'
                }`}
              >
                <p className={`text-xs font-semibold ${q.tone === 'bad' ? 'text-bad' : q.tone === 'warn' ? 'text-warn' : 'text-accent'}`}>
                  {q.title}
                </p>
                <p className="mt-0.5 text-[11px] leading-snug text-ink-muted">{q.detail}</p>
              </div>
            ))
          )}
        </div>
      </Card>

      {/* Best current opportunities */}
      <Card>
        <CardHeader
          title="Best current opportunities"
          hint="Only selections passing all required data and risk checks; sorted by conservative edge"
        />
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <caption className="sr-only">Best current opportunities with model probabilities and recommended stakes</caption>
            <thead>
              <tr>
                <Th>Game</Th><Th>Market</Th><Th>Selection</Th><Th>Line</Th><Th>Price</Th>
                <Th>Model prob</Th><Th>Break-even</Th><Th>Edge</Th><Th>Stake</Th>
                <Th>Confidence</Th><Th>Updated</Th><Th>Status</Th>
              </tr>
            </thead>
            <tbody>
              {best.length === 0 ? (
                <tr><Td colSpan={12}><EmptyState title="No qualifying opportunities" hint="This is the expected normal state. Pass is the default posture." /></Td></tr>
              ) : (
                best.map((r) => (
                  <tr key={r.id} className="hover:bg-panel-raised/60">
                    <Td>
                      <Link className="text-accent underline-offset-2 hover:underline" to={`/game/${r.gameId}`}>
                        {gameLabel(ds, r.gameId)}
                      </Link>
                    </Td>
                    <Td>{r.market}</Td>
                    <Td>{r.selection}</Td>
                    <Td><Mono>{fmtMarketLine(r.market, r.line)}</Mono></Td>
                    <Td><Mono>{fmtOdds(r.american, store.settings.oddsFormat)}</Mono></Td>
                    <Td><Mono>{fmtPct(r.conservativeProbability)}</Mono></Td>
                    <Td><Mono>{fmtPct(r.breakEvenProbability)}</Mono></Td>
                    <Td><Mono className={r.edge > 0 ? 'text-ok' : ''}>{fmtSignedPct(r.edge)}</Mono></Td>
                    <Td><Mono>{r.stake ? fmtMoney(r.stake.finalStakeAmount) : '—'}</Mono></Td>
                    <Td>{r.confidence}</Td>
                    <Td><Mono>{fmtAgo(r.createdAt, ds.demoNow)}</Mono></Td>
                    <Td><RecBadge status={r.status} /></Td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
