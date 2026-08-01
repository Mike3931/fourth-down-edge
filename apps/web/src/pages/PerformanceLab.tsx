import { useMemo, useState } from 'react';
import { Card, CardHeader, ErrorState, LoadingState, Mono, SectionLabel, Stat, Td, Th } from '@fde/ui';
import {
  brierScore, calibrationBins, calibrationLine, expectedCalibrationError, logLoss, maxDrawdown,
} from '@fde/calculations';
import { useDataset } from '../lib/api';
import { CurveChart, ReliabilityChart } from '../components/charts';
import { fmtMoney, fmtPct } from '../lib/format';

export default function PerformanceLab() {
  const { data: ds, isLoading, error } = useDataset();

  const [statusFilter, setStatusFilter] = useState('ALL (incl. passes)');
  const [marketFilter, setMarketFilter] = useState('ALL');
  const [edgeTier, setEdgeTier] = useState('ALL');
  const [venueFilter, setVenueFilter] = useState('ALL');
  const [sideFilter, setSideFilter] = useState('ALL');
  const [horizonFilter, setHorizonFilter] = useState('ALL');
  const [dataTier, setDataTier] = useState('ALL');
  const [weekFilter, setWeekFilter] = useState('ALL');

  const records = useMemo(() => {
    if (!ds) return [];
    return ds.backtest.filter((r) => {
      if (statusFilter === 'BET only' && r.recStatus !== 'BET') return false;
      if (statusFilter === 'BET + WATCH' && !(r.recStatus === 'BET' || r.recStatus === 'WATCH')) return false;
      if (marketFilter !== 'ALL' && r.market !== marketFilter) return false;
      if (edgeTier === '≥3%' && r.edge < 0.03) return false;
      if (edgeTier === '1–3%' && (r.edge < 0.01 || r.edge >= 0.03)) return false;
      if (edgeTier === '<1%' && r.edge >= 0.01) return false;
      if (venueFilter === 'INDOOR' && !r.indoor) return false;
      if (venueFilter === 'OUTDOOR' && r.indoor) return false;
      if (sideFilter === 'FAVORITES' && !r.favorite) return false;
      if (sideFilter === 'UNDERDOGS' && r.favorite) return false;
      if (horizonFilter === '<24h' && r.hoursBeforeKickoff >= 24) return false;
      if (horizonFilter === '≥24h' && r.hoursBeforeKickoff < 24) return false;
      if (dataTier === 'HIGH (≥90%)' && r.dataCompleteness < 0.9) return false;
      if (dataTier === 'LOW (<90%)' && r.dataCompleteness >= 0.9) return false;
      if (weekFilter !== 'ALL' && r.week !== Number(weekFilter)) return false;
      return true;
    });
  }, [ds, statusFilter, marketFilter, edgeTier, venueFilter, sideFilter, horizonFilter, dataTier, weekFilter]);

  const startingBalance = ds?.bankrollAccount.startingBalance ?? 0;

  const metrics = useMemo(() => {
    const pairs = records.map((r) => ({ p: r.p, outcome: r.outcome }));
    const bins = calibrationBins(pairs);
    const line = calibrationLine(pairs);
    const wagers = records.filter((r) => r.stake > 0);
    const staked = wagers.reduce((a, r) => a + r.stake, 0);
    const profit = wagers.reduce((a, r) => a + r.profit, 0);
    let bal = startingBalance;
    const curve = [bal];
    for (const r of records) {
      bal += r.profit;
      curve.push(bal);
    }
    const decidedWagers = wagers.filter((r) => !r.push);
    const wins = decidedWagers.filter((r) => r.outcome === 1).length;
    return {
      logLoss: logLoss(pairs),
      brier: brierScore(pairs),
      ece: expectedCalibrationError(bins, pairs.length),
      slope: line.slope,
      intercept: line.intercept,
      meanClv: records.length ? records.reduce((a, r) => a + r.clvPct, 0) / records.length : NaN,
      roi: staked > 0 ? profit / staked : NaN,
      mdd: maxDrawdown(curve),
      winRate: decidedWagers.length ? wins / decidedWagers.length : NaN,
      pushRate: records.length ? records.filter((r) => r.push).length / records.length : NaN,
      avgEdge: records.length ? records.reduce((a, r) => a + r.edge, 0) / records.length : NaN,
      nRecs: records.length,
      nWagers: wagers.length,
      avgStake: wagers.length ? staked / wagers.length : 0,
      coverage: records.length
        ? records.filter((r) => r.withinInterval80).length / records.length
        : NaN,
      bins,
      curve,
      pairs,
    };
  }, [records, startingBalance]);

  if (isLoading) return <LoadingState label="Computing performance metrics…" />;
  if (error || !ds) return <ErrorState title="Failed to load performance data" />;

  const clvTrend = records.map((r, i) => ({ x: i + 1, y: r.clvPct }));
  const cumClv: Array<{ x: number; y: number }> = [];
  let acc = 0;
  records.forEach((r, i) => { acc += r.clvPct; cumClv.push({ x: i + 1, y: acc / (i + 1) }); });
  const drawdownCurve = metrics.curve.map((v, i) => {
    const peak = Math.max(...metrics.curve.slice(0, i + 1));
    return { x: i, y: peak > 0 ? ((v - peak) / peak) * 100 : 0 };
  });

  // Edge-tier and recommendation slices for the tables.
  const tiers = [
    { name: '<1%', rs: records.filter((r) => r.edge < 0.01) },
    { name: '1–3%', rs: records.filter((r) => r.edge >= 0.01 && r.edge < 0.03) },
    { name: '≥3%', rs: records.filter((r) => r.edge >= 0.03) },
  ];
  const recSlices = ['BET', 'WATCH', 'PASS', 'DATA INCOMPLETE'].map((s) => ({
    name: s,
    rs: records.filter((r) => r.recStatus === s),
  }));

  const Select = ({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: string[] }) => (
    <label className="flex items-center gap-1.5 text-[11px] text-ink-faint">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)} className="rounded border border-edge bg-panel-raised px-1.5 py-1 text-[11px] text-ink focus:border-accent focus:outline-none">
        {options.map((o) => <option key={o}>{o}</option>)}
      </select>
    </label>
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Performance Lab — synthetic demo backtest"
          hint="Defaults to ALL eligible predictions including passes. Win rate is deliberately not the headline metric; calibration and closing-line value are."
        />
        <div className="flex flex-wrap items-center gap-3 border-b border-edge px-3 py-2">
          <Select label="Status" value={statusFilter} onChange={setStatusFilter} options={['ALL (incl. passes)', 'BET + WATCH', 'BET only']} />
          <Select label="Market" value={marketFilter} onChange={setMarketFilter} options={['ALL', 'MONEYLINE', 'SPREAD', 'TOTAL']} />
          <Select label="Edge tier" value={edgeTier} onChange={setEdgeTier} options={['ALL', '<1%', '1–3%', '≥3%']} />
          <Select label="Venue" value={venueFilter} onChange={setVenueFilter} options={['ALL', 'INDOOR', 'OUTDOOR']} />
          <Select label="Side" value={sideFilter} onChange={setSideFilter} options={['ALL', 'FAVORITES', 'UNDERDOGS']} />
          <Select label="Horizon" value={horizonFilter} onChange={setHorizonFilter} options={['ALL', '<24h', '≥24h']} />
          <Select label="Data tier" value={dataTier} onChange={setDataTier} options={['ALL', 'HIGH (≥90%)', 'LOW (<90%)']} />
          <Select label="Week" value={weekFilter} onChange={setWeekFilter} options={['ALL', ...Array.from({ length: 18 }, (_, i) => String(i + 1))]} />
        </div>
        <section aria-label="Primary metrics" className="grid grid-cols-2 gap-2 p-3 md:grid-cols-5">
          <Stat label="Log loss" value={metrics.logLoss.toFixed(4)} sub="lower is better; 0.693 = coin flip" />
          <Stat label="Brier score" value={metrics.brier.toFixed(4)} sub="lower is better; 0.25 = coin flip" />
          <Stat label="Calibration error (ECE)" value={fmtPct(metrics.ece, 2)} />
          <Stat label="Calibration slope / intercept" value={`${metrics.slope.toFixed(2)} / ${metrics.intercept.toFixed(2)}`} sub="ideal 1.00 / 0.00" />
          <Stat label="Mean CLV" value={`${metrics.meanClv >= 0 ? '+' : ''}${metrics.meanClv.toFixed(2)} pts`} tone={metrics.meanClv > 0 ? 'positive' : 'default'} />
          <Stat label="ROI after vig" value={Number.isNaN(metrics.roi) ? '—' : fmtPct(metrics.roi, 2)} sub="staked records only" />
          <Stat label="Max drawdown" value={fmtPct(metrics.mdd, 2)} tone="warning" />
          <Stat label="Win rate" value={Number.isNaN(metrics.winRate) ? '—' : fmtPct(metrics.winRate)} sub="secondary metric by design; pushes excluded" />
          <Stat label="Push rate" value={Number.isNaN(metrics.pushRate) ? '—' : fmtPct(metrics.pushRate)} />
          <Stat label="Avg edge" value={fmtPct(metrics.avgEdge, 2)} />
          <Stat label="Recommendations" value={metrics.nRecs} />
          <Stat label="Wagers" value={metrics.nWagers} />
          <Stat label="Avg stake" value={`$${metrics.avgStake.toFixed(0)}`} />
          <Stat label="80% interval coverage" value={Number.isNaN(metrics.coverage) ? '—' : fmtPct(metrics.coverage, 0)} sub="target 80%" />
          <Stat label="Model vs market" value={`${metrics.meanClv >= 0 ? '+' : ''}${metrics.meanClv.toFixed(2)} pts CLV`} sub="closing market as benchmark" tone="model" />
        </section>
      </Card>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card className="p-3">
          <SectionLabel>Reliability diagram — predicted vs observed</SectionLabel>
          <ReliabilityChart
            bins={metrics.bins}
            label="Reliability diagram"
            textSummary={`Expected calibration error ${fmtPct(metrics.ece, 2)}; slope ${metrics.slope.toFixed(2)} (ideal 1.00). Points below the diagonal indicate overconfidence in that band.`}
          />
        </Card>
        <Card className="p-3">
          <SectionLabel>Closing-line value trend (per-record and running mean)</SectionLabel>
          <CurveChart
            points={cumClv}
            tone="#3fb96a"
            refY={0}
            yFormatter={(v) => `${v.toFixed(1)}`}
            label="Running mean closing line value"
            textSummary={`Running mean CLV across ${records.length} records ends at ${cumClv.at(-1)?.y.toFixed(2) ?? '—'} probability points. Positive means beating the closing market on average.`}
          />
          <div className="mt-2">
            <CurveChart
              points={clvTrend}
              tone="#37b8c8"
              refY={0}
              yFormatter={(v) => v.toFixed(0)}
              label="Per-record closing line value"
              textSummary="Per-record CLV in probability points; individual records are noisy by nature."
            />
          </div>
        </Card>
        <Card className="p-3">
          <SectionLabel>Bankroll curve (staked records)</SectionLabel>
          <CurveChart
            points={metrics.curve.map((v, i) => ({ x: i, y: v }))}
            yFormatter={(v) => `$${(v / 1000).toFixed(1)}k`}
            label="Bankroll curve"
            textSummary={`Bankroll moves from ${fmtMoney(startingBalance)} to ${fmtMoney(metrics.curve.at(-1) ?? startingBalance)} across the filtered demo backtest.`}
          />
        </Card>
        <Card className="p-3">
          <SectionLabel>Drawdown curve</SectionLabel>
          <CurveChart
            points={drawdownCurve}
            tone="#e05d5d"
            yFormatter={(v) => `${v.toFixed(1)}%`}
            label="Drawdown curve"
            textSummary={`Maximum drawdown ${fmtPct(metrics.mdd, 2)}.`}
          />
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader title="Performance by edge tier" />
          <SliceTable slices={tiers} />
        </Card>
        <Card>
          <CardHeader title="Performance by recommendation" />
          <SliceTable slices={recSlices} />
        </Card>
      </div>

      <p className="text-[11px] text-ink-faint">
        All metrics above are computed from a synthetic demonstration backtest generated deterministically for UI
        development. They do not describe any real model's performance and must not inform real-money decisions.
      </p>
    </div>
  );
}

function SliceTable({ slices }: { slices: Array<{ name: string; rs: Array<{ p: number; outcome: 0 | 1; clvPct: number; edge: number }> }> }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead><tr><Th>Slice</Th><Th>N</Th><Th>Log loss</Th><Th>Brier</Th><Th>Mean CLV</Th><Th>Avg edge</Th></tr></thead>
        <tbody>
          {slices.map((s) => {
            const pairs = s.rs.map((r) => ({ p: r.p, outcome: r.outcome }));
            return (
              <tr key={s.name}>
                <Td className="font-medium">{s.name}</Td>
                <Td><Mono>{s.rs.length}</Mono></Td>
                <Td><Mono>{s.rs.length ? logLoss(pairs).toFixed(4) : '—'}</Mono></Td>
                <Td><Mono>{s.rs.length ? brierScore(pairs).toFixed(4) : '—'}</Mono></Td>
                <Td><Mono>{s.rs.length ? (s.rs.reduce((a, r) => a + r.clvPct, 0) / s.rs.length).toFixed(2) : '—'}</Mono></Td>
                <Td><Mono>{s.rs.length ? `${((s.rs.reduce((a, r) => a + r.edge, 0) / s.rs.length) * 100).toFixed(2)}%` : '—'}</Mono></Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
