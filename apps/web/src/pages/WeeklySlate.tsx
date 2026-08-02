import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel,
  useReactTable, type SortingState,
} from '@tanstack/react-table';
import { Card, CardHeader, ErrorState, LoadingState, Mono, RecBadge, cn } from '@fde/ui';
import type { Recommendation, RecommendationStatus } from '@fde/shared-types';
import { useDataset, useRecommendations } from '../lib/api';
import { useStore } from '../lib/store';
import { latestSnapshotForDisplay, type DemoDataset } from '@fde/api-client';
import { fmtKickoff, fmtLine, fmtOdds, fmtPct, fmtSigned, fmtUtc } from '../lib/format';
import { modelVersionLabel, stadiumById, teamById } from '../lib/joins';

interface SlateRow {
  gameId: string;
  kickoffUtc: string;
  away: string;
  home: string;
  stadium: string;
  roof: string;
  consensusSpread?: number;
  modelSpread: number;
  spreadDiff: number;
  consensusTotal?: number;
  modelTotal: number;
  totalDiff: number;
  homeWinProb: number;
  bestMarket: string;
  bestEdge: number;
  recommendation: RecommendationStatus;
  injurySeverity: string;
  weatherSeverity: string;
  completeness: number;
  predictionAt: string;
  modelVersion: string;
  rec: Recommendation;
}

function buildRows(ds: DemoDataset, recs: Recommendation[]): SlateRow[] {
  return ds.games.map((g) => {
    const pred = ds.predictions.find((p) => p.gameId === g.id && p.isOfficial)!;
    const rec = recs.find((r) => r.gameId === g.id)!;
    const spread = latestSnapshotForDisplay(ds.oddsSnapshots, g.id, 'SPREAD');
    const total = latestSnapshotForDisplay(ds.oddsSnapshots, g.id, 'TOTAL');
    const injuries = ds.availabilitySnapshots.filter((a) => a.gameId === g.id);
    const worstInjury = injuries.reduce((worst, a) => Math.max(worst, (1 - a.activeProbability) * a.estimatedTeamImpactPts), 0);
    const wx = ds.weatherSnapshots.find((w) => w.gameId === g.id);
    const modelSpread = -pred.expectedMargin;
    const modelTotal = pred.expectedTotal;
    return {
      gameId: g.id,
      kickoffUtc: g.kickoffUtc,
      away: teamById(ds, g.awayTeamId).abbreviation,
      home: teamById(ds, g.homeTeamId).abbreviation,
      stadium: stadiumById(ds, g.stadiumId)?.name ?? '—',
      roof: g.roofStatus,
      consensusSpread: spread?.line,
      modelSpread,
      spreadDiff: spread?.line !== undefined ? modelSpread - spread.line : 0,
      consensusTotal: total?.line,
      modelTotal,
      totalDiff: total?.line !== undefined ? modelTotal - total.line : 0,
      homeWinProb: pred.homeWinProbability,
      bestMarket: `${rec.market} ${rec.selection}`,
      bestEdge: rec.edge,
      recommendation: rec.status,
      injurySeverity: worstInjury > 2.5 ? 'HIGH' : worstInjury > 1 ? 'MODERATE' : worstInjury > 0 ? 'LOW' : 'NONE',
      weatherSeverity: g.roofStatus === 'DOME' || g.roofStatus === 'RETRACTABLE_CLOSED' ? 'NONE' : wx?.severity ?? 'MISSING',
      completeness: pred.dataCompletenessScore,
      predictionAt: pred.asOfAt,
      modelVersion: modelVersionLabel(ds, pred.modelVersionId),
      rec,
    };
  });
}

const col = createColumnHelper<SlateRow>();

export default function WeeklySlate() {
  const { data: ds, isLoading, error } = useDataset();
  const { data: recs } = useRecommendations();
  const store = useStore();
  const [sorting, setSorting] = useState<SortingState>([{ id: 'kickoffUtc', desc: false }]);

  const [recFilter, setRecFilter] = useState<string>('ALL');
  const [marketFilter, setMarketFilter] = useState<string>('ALL');
  const [minEdge, setMinEdge] = useState<number>(0);
  const [completenessFilter, setCompletenessFilter] = useState<string>('ALL');
  const [injuryFilter, setInjuryFilter] = useState<string>('ALL');
  const [weatherFilter, setWeatherFilter] = useState<string>('ALL');
  const [windowFilter, setWindowFilter] = useState<string>('ALL');
  const [roofFilter, setRoofFilter] = useState<string>('ALL');
  const [sideFilter, setSideFilter] = useState<string>('ALL');

  const rows = useMemo(() => (ds && recs ? buildRows(ds, recs) : []), [ds, recs]);

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (recFilter !== 'ALL' && r.recommendation !== recFilter) return false;
      if (marketFilter !== 'ALL' && !r.bestMarket.startsWith(marketFilter)) return false;
      if (minEdge > 0 && r.bestEdge * 100 < minEdge) return false;
      if (completenessFilter === 'HIGH' && r.completeness < 0.9) return false;
      if (completenessFilter === 'LOW' && r.completeness >= 0.9) return false;
      if (injuryFilter !== 'ALL' && r.injurySeverity !== injuryFilter) return false;
      if (weatherFilter !== 'ALL' && r.weatherSeverity !== weatherFilter) return false;
      if (windowFilter !== 'ALL') {
        const h = new Date(r.kickoffUtc).getUTCHours();
        const day = new Date(r.kickoffUtc).getUTCDay();
        const window = day === 5 || day === 1 || day === 2 ? 'PRIMETIME' : h < 19 ? 'SUN_EARLY' : 'SUN_LATE';
        if (window !== windowFilter) return false;
      }
      if (roofFilter === 'INDOOR' && !(r.roof === 'DOME' || r.roof === 'RETRACTABLE_CLOSED')) return false;
      if (roofFilter === 'OUTDOOR' && (r.roof === 'DOME' || r.roof === 'RETRACTABLE_CLOSED')) return false;
      if (sideFilter === 'FAVORITES' && (r.consensusSpread ?? 0) > 0) return false;
      if (sideFilter === 'UNDERDOGS' && (r.consensusSpread ?? 0) <= 0) return false;
      return true;
    });
  }, [rows, recFilter, marketFilter, minEdge, completenessFilter, injuryFilter, weatherFilter, windowFilter, roofFilter, sideFilter]);

  const columns = useMemo(
    () => [
      col.accessor('kickoffUtc', {
        header: 'Kickoff',
        cell: (c) => (
          <Mono
            tabIndex={0}
            title={`${c.getValue()} (UTC)`}
            aria-label={`${fmtKickoff(c.getValue(), store.settings.timezone)}, ${c.getValue()} UTC`}
            className="cursor-help focus:outline focus:outline-2 focus:outline-accent"
          >
            {fmtKickoff(c.getValue(), store.settings.timezone)}
          </Mono>
        ),
      }),
      col.accessor('away', { header: 'Away' }),
      col.accessor('home', { header: 'Home' }),
      col.accessor('stadium', { header: 'Stadium', cell: (c) => <span className="text-ink-muted">{c.getValue()}</span> }),
      col.accessor('consensusSpread', { header: 'Cons spread', cell: (c) => <Mono>{fmtLine(c.getValue())}</Mono> }),
      col.accessor('modelSpread', { header: 'Model spread', cell: (c) => <Mono className="text-model">{fmtLine(Math.round(c.getValue() * 10) / 10)}</Mono> }),
      col.accessor('spreadDiff', {
        header: 'Δ spread',
        cell: (c) => <Mono className={Math.abs(c.getValue()) >= 1.5 ? 'text-accent' : 'text-ink-faint'}>{fmtSigned(c.getValue())}</Mono>,
      }),
      col.accessor('consensusTotal', { header: 'Cons total', cell: (c) => <Mono>{c.getValue() ?? '—'}</Mono> }),
      col.accessor('modelTotal', { header: 'Model total', cell: (c) => <Mono className="text-model">{c.getValue().toFixed(1)}</Mono> }),
      col.accessor('totalDiff', {
        header: 'Δ total',
        cell: (c) => <Mono className={Math.abs(c.getValue()) >= 2 ? 'text-accent' : 'text-ink-faint'}>{fmtSigned(c.getValue())}</Mono>,
      }),
      col.accessor('homeWinProb', { header: 'Home win %', cell: (c) => <Mono>{fmtPct(c.getValue())}</Mono> }),
      col.accessor('bestMarket', { header: 'Best market' }),
      col.accessor('bestEdge', {
        header: 'Best edge',
        cell: (c) => <Mono className={c.getValue() > 0.02 ? 'text-ok' : 'text-ink-muted'}>{fmtPct(c.getValue())}</Mono>,
      }),
      col.accessor('recommendation', { header: 'Rec', cell: (c) => <RecBadge status={c.getValue()} /> }),
      col.accessor('injurySeverity', {
        header: 'Injury',
        cell: (c) => <span className={sevCls(c.getValue())}>{c.getValue()}</span>,
      }),
      col.accessor('weatherSeverity', {
        header: 'Weather',
        cell: (c) => <span className={sevCls(c.getValue())}>{c.getValue()}</span>,
      }),
      col.accessor('completeness', {
        header: 'Data',
        cell: (c) => <Mono className={c.getValue() >= 0.85 ? 'text-ok' : 'text-bad'}>{fmtPct(c.getValue(), 0)}</Mono>,
      }),
      col.accessor('predictionAt', { header: 'Pred @ (UTC)', cell: (c) => <Mono className="text-ink-faint">{fmtUtc(c.getValue())}</Mono> }),
      col.accessor('modelVersion', { header: 'Model', cell: (c) => <Mono className="text-model">{c.getValue()}</Mono> }),
    ],
    [store.settings.timezone],
  );

  const table = useReactTable({
    data: filtered,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (isLoading) return <LoadingState label="Loading weekly slate…" />;
  if (error || !ds) return <ErrorState title="Failed to load slate" />;

  const Select = ({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: string[] }) => (
    <label className="flex items-center gap-1.5 text-[11px] text-ink-faint">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-edge bg-panel-raised px-1.5 py-1 text-[11px] text-ink focus:border-accent focus:outline-none"
      >
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          title={`Weekly slate — ${filtered.length} of ${rows.length} games`}
          hint="Click any game to open it in Game Lab. Click headers to sort."
        />
        <div className="flex flex-wrap items-center gap-3 border-b border-edge px-3 py-2">
          <Select label="Rec" value={recFilter} onChange={setRecFilter} options={['ALL', 'BET', 'WATCH', 'PASS', 'DATA INCOMPLETE']} />
          <Select label="Market" value={marketFilter} onChange={setMarketFilter} options={['ALL', 'SPREAD', 'TOTAL', 'MONEYLINE']} />
          <label className="flex items-center gap-1.5 text-[11px] text-ink-faint">
            Min edge %
            <input
              type="number" step={0.5} min={0} max={10} value={minEdge}
              onChange={(e) => setMinEdge(Number(e.target.value))}
              className="w-14 rounded border border-edge bg-panel-raised px-1.5 py-1 font-mono text-[11px] text-ink focus:border-accent focus:outline-none"
            />
          </label>
          <Select label="Data" value={completenessFilter} onChange={setCompletenessFilter} options={['ALL', 'HIGH', 'LOW']} />
          <Select label="Injury" value={injuryFilter} onChange={setInjuryFilter} options={['ALL', 'NONE', 'LOW', 'MODERATE', 'HIGH']} />
          <Select label="Weather" value={weatherFilter} onChange={setWeatherFilter} options={['ALL', 'NONE', 'LOW', 'MODERATE', 'HIGH', 'MISSING']} />
          <Select label="Window" value={windowFilter} onChange={setWindowFilter} options={['ALL', 'SUN_EARLY', 'SUN_LATE', 'PRIMETIME']} />
          <Select label="Venue" value={roofFilter} onChange={setRoofFilter} options={['ALL', 'INDOOR', 'OUTDOOR']} />
          <Select label="Side" value={sideFilter} onChange={setSideFilter} options={['ALL', 'FAVORITES', 'UNDERDOGS']} />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <caption className="sr-only">Weekly slate of demonstration games with market and model numbers</caption>
            <thead>
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((h) => (
                    <th
                      key={h.id}
                      scope="col"
                      aria-sort={h.column.getIsSorted() === 'asc' ? 'ascending' : h.column.getIsSorted() === 'desc' ? 'descending' : 'none'}
                      className="sticky top-0 z-10 whitespace-nowrap border-b border-edge bg-panel-raised px-2.5 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-ink-faint"
                    >
                      <button
                        onClick={h.column.getToggleSortingHandler()}
                        className="inline-flex items-center gap-1 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                      >
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        <span aria-hidden="true">{{ asc: '↑', desc: '↓' }[h.column.getIsSorted() as string] ?? ''}</span>
                      </button>
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr key={row.id} className="hover:bg-panel-raised/60">
                  {row.getVisibleCells().map((cell, ci) => (
                    <td key={cell.id} className="whitespace-nowrap border-b border-edge/60 px-2.5 py-1.5 text-xs">
                      {ci === 1 ? (
                        <Link to={`/game/${row.original.gameId}`} className="text-accent underline-offset-2 hover:underline">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </Link>
                      ) : (
                        flexRender(cell.column.columnDef.cell, cell.getContext())
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="px-3 py-2 text-[11px] text-ink-faint">
          Best available market shows the selection with the highest conservative edge. Prices:{' '}
          <Mono>{fmtOdds(-110, store.settings.oddsFormat)}</Mono>-style consensus, clearly mocked in v1.
        </p>
      </Card>
    </div>
  );
}

function sevCls(sev: string): string {
  return cn(
    'text-[11px] font-medium',
    sev === 'HIGH' || sev === 'MISSING' ? 'text-bad' : sev === 'MODERATE' ? 'text-warn' : sev === 'LOW' ? 'text-ink-muted' : 'text-ink-faint',
  );
}
