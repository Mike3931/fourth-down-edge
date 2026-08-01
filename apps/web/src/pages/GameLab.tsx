import { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  Card, CardHeader, ErrorState, FreshBadge, LoadingState, Mono, Pill, SectionLabel, Stat, Td, Th,
} from '@fde/ui';
import {
  centralInterval, marginDistribution, spreadOutcomeProbabilities,
  totalDistribution, totalOutcomeProbabilities,
} from '@fde/calculations';
import type { FactorAssessment } from '@fde/shared-types';
import { latestSnapshot, marginToWinProb, type DemoDataset } from '@fde/api-client';
import { useDataset, useRecommendations } from '../lib/api';
import { useStore } from '../lib/store';
import { CoverByLineChart, DistributionChart } from '../components/charts';
import BetCard from '../components/BetCard';
import ResearchPanel from '../components/ResearchPanel';
import { fmtKickoff, fmtLine, fmtNum, fmtOdds, fmtPct, fmtSigned, fmtUtc } from '../lib/format';
import { gameById, modelVersionLabel, playerById, roofLabel, stadiumById, teamById } from '../lib/joins';
import { Rng } from '@fde/api-client';

const FACTOR_NAMES = [
  'Quarterback', 'Passing offense', 'Rushing offense', 'Offensive line', 'Receiving corps',
  'Pass defense', 'Run defense', 'Pass rush', 'Coverage', 'Special teams', 'Coaching',
  'Pace', 'Rest', 'Travel', 'Venue', 'Weather', 'Officials',
] as const;

function ordinal(n: number): string {
  const rem10 = n % 10;
  const rem100 = n % 100;
  if (rem10 === 1 && rem100 !== 11) return `${n}st`;
  if (rem10 === 2 && rem100 !== 12) return `${n}nd`;
  if (rem10 === 3 && rem100 !== 13) return `${n}rd`;
  return `${n}th`;
}

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function buildFactors(ds: DemoDataset, gameId: string): FactorAssessment[] {
  const game = gameById(ds, gameId);
  // A URL can name a game that isn't on this slate; the caller renders an
  // "Unknown game" state for that, so produce nothing rather than throwing.
  if (!game) return [];
  const rng = new Rng(hashString(gameId));
  const wx = ds.weatherSnapshots.find((w) => w.gameId === gameId);
  const crew = ds.officials.find((o) => o.id === game.officialCrewId);
  return FACTOR_NAMES.map((factor) => {
    const effect = Math.round(rng.normal(0, 1.1) * 10) / 10;
    const conf = rng.pick(['LOW', 'MEDIUM', 'HIGH'] as const);
    let rawData = `${ordinal(rng.int(38, 68))} percentile differential (demo metric)`;
    let modelMetric = `Opponent-adjusted composite ${fmtSigned(effect * 1.4)} (demo)`;
    if (factor === 'Weather') {
      rawData = wx ? `Wind ${wx.windMph} mph, gusts ${wx.gustMph}, precip ${Math.round(wx.precipitationChance * 100)}%` : 'No usable forecast';
      modelMetric = wx ? `Total adjustment ${fmtSigned(-(wx.windMph - 8) * 0.12)} pts` : 'MISSING';
    }
    if (factor === 'Officials') {
      rawData = crew ? `${crew.refereeName}: ${crew.crewPenaltyRatePerGame} pen/gm, over rate ${fmtPct(crew.crewOverRate, 0)} (n=${crew.sampleGames})` : 'Unassigned';
      modelMetric = 'Referee effect held at 0 pending larger sample';
    }
    if (factor === 'Venue') {
      const stad = stadiumById(ds, game.stadiumId);
      rawData = stad ? `${stad.name}: ${stad.surface}, ${stad.roof}, ${stad.altitudeFt} ft` : '—';
      modelMetric = `Home-field prior +1.4 pts (league demo prior)`;
    }
    return {
      factor,
      rawData,
      modelMetric,
      estimatedEffect: `${fmtSigned(effect)} pts to home margin`,
      confidence: conf,
      sourceTimestamp: wx && factor === 'Weather' ? wx.observedAt : ds.demoNow,
      likelyPricedIntoMarket: rng.bool(0.7),
      favors: effect > 0.3 ? 'HOME' : effect < -0.3 ? 'AWAY' : 'NEUTRAL',
    };
  });
}

export default function GameLab() {
  const { gameId = '' } = useParams();
  const { data: ds, isLoading } = useDataset();
  const { data: recs } = useRecommendations();
  const store = useStore();

  // Exploratory scenario state — never touches the official prediction.
  const [qbOut, setQbOut] = useState(false);
  const [qbRestricted, setQbRestricted] = useState(false);
  const [windDelta, setWindDelta] = useState(0);
  const [roofClosed, setRoofClosed] = useState<boolean | null>(null);
  const [snapShareDelta, setSnapShareDelta] = useState(0);

  const factors = useMemo(() => (ds ? buildFactors(ds, gameId) : []), [ds, gameId]);

  if (isLoading || !ds) return <LoadingState label="Loading Game Lab…" />;
  const game = gameById(ds, gameId);
  if (!game) return <ErrorState title="Unknown game" detail={`No game with id ${gameId} on the demo slate.`} />;

  const away = teamById(ds, game.awayTeamId);
  const home = teamById(ds, game.homeTeamId);
  const stadium = stadiumById(ds, game.stadiumId);
  const pred = ds.predictions.find((p) => p.gameId === gameId && p.isOfficial)!;
  const allVintages = ds.predictions
    .filter((p) => p.gameId === gameId)
    .sort((a, b) => a.asOfAt.localeCompare(b.asOfAt));
  const rec = recs?.find((r) => r.gameId === gameId);
  const spread = latestSnapshot(ds.oddsSnapshots, gameId, 'SPREAD');
  const total = latestSnapshot(ds.oddsSnapshots, gameId, 'TOTAL');
  const ml = latestSnapshot(ds.oddsSnapshots, gameId, 'MONEYLINE');
  const components = ds.predictionComponents.filter((c) => c.predictionId === pred.id);

  const mDist = marginDistribution(pred.expectedMargin, pred.marginStd);
  const tDist = totalDistribution(pred.expectedTotal, pred.totalStd);
  const spreadLine = spread?.line ?? 0;
  const pushAtSpread = spreadOutcomeProbabilities(mDist, spreadLine).push;

  // Scenario (exploratory) adjustments.
  const qbAvail = ds.availabilitySnapshots.filter((a) => {
    const pl = playerById(ds, a.playerId);
    return a.gameId === gameId && pl?.position === 'QB';
  })[0];
  const qbImpact = qbAvail?.estimatedTeamImpactPts ?? 4.5;
  let scenarioMargin = pred.expectedMargin;
  let scenarioTotal = pred.expectedTotal;
  if (qbOut) { scenarioMargin -= qbImpact; scenarioTotal -= 2.0; }
  else if (qbRestricted) { scenarioMargin -= qbImpact * 0.4; scenarioTotal -= 0.8; }
  scenarioTotal -= windDelta * 0.12;
  if (roofClosed === true) scenarioTotal += 0.8;
  if (roofClosed === false) scenarioTotal -= 0.4;
  scenarioMargin += snapShareDelta * 0.05;
  const scenarioActive = qbOut || qbRestricted || windDelta !== 0 || roofClosed !== null || snapShareDelta !== 0;
  const sDist = marginDistribution(scenarioMargin, pred.marginStd + (scenarioActive ? 0.6 : 0));
  const sTotalDist = totalDistribution(scenarioTotal, pred.totalStd + (scenarioActive ? 0.5 : 0));
  const scenarioHomeWp = sDist.filter((p) => p.value > 0).reduce((a, p) => a + p.probability, 0);

  const coverPoints = [];
  for (let line = Math.round(spreadLine) - 7; line <= Math.round(spreadLine) + 7; line += 1) {
    coverPoints.push({ line, prob: spreadOutcomeProbabilities(mDist, line).cover });
  }
  const overPoints = [];
  for (let line = Math.round(pred.expectedTotal) - 8; line <= Math.round(pred.expectedTotal) + 8; line += 1) {
    overPoints.push({ line, prob: totalOutcomeProbabilities(tDist, line).over });
  }
  const keyNumbers = [3, 7, 6, 10, 14].map((k) => ({
    k,
    home: mDist.filter((p) => p.value === k).reduce((a, p) => a + p.probability, 0),
    away: mDist.filter((p) => p.value === -k).reduce((a, p) => a + p.probability, 0),
  }));

  const health = ds.feedStatuses.filter((f) => f.impactedGameIds.includes(gameId));

  return (
    <div className="space-y-4">
      {/* Header */}
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3 p-4">
          <div>
            <h1 className="text-lg font-semibold text-ink">
              {away.name} <span className="text-ink-faint">at</span> {home.name}
            </h1>
            <p className="mt-1 text-xs text-ink-muted">
              {fmtKickoff(game.kickoffUtc, store.settings.timezone)} · {stadium?.name}, {stadium?.city} ·{' '}
              {stadium?.surface} · {roofLabel(game.roofStatus)}
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Pill tone="neutral">spread <Mono>{fmtLine(spread?.line)}</Mono></Pill>
              <Pill tone="neutral">total <Mono>{total?.line ?? '—'}</Mono></Pill>
              <Pill tone="neutral">
                ML <Mono>{ml ? `${fmtOdds(ml.awayAmerican, store.settings.oddsFormat)} / ${fmtOdds(ml.homeAmerican, store.settings.oddsFormat)}` : '—'}</Mono>
              </Pill>
              <Pill tone="model">model {modelVersionLabel(ds, pred.modelVersionId)}</Pill>
              <Pill tone="neutral" title="Prediction cutoff (as-of) timestamp">as-of <Mono>{fmtUtc(pred.asOfAt)}</Mono></Pill>
              <Pill tone={pred.dataCompletenessScore >= 0.85 ? 'ok' : 'bad'}>
                data {fmtPct(pred.dataCompletenessScore, 0)}
              </Pill>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            {health.length === 0 ? (
              <FreshBadge status="CURRENT" />
            ) : (
              health.map((h) => (
                <div key={h.feed} className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                  {h.feed} <FreshBadge status={h.status} />
                </div>
              ))
            )}
          </div>
        </div>
      </Card>

      {/* Expected outcome */}
      <section aria-label="Expected outcome" className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <Stat label={`${away.abbreviation} expected`} value={fmtNum(pred.expectedAwayScore)} />
        <Stat label={`${home.abbreviation} expected`} value={fmtNum(pred.expectedHomeScore)} />
        <Stat label="Expected margin (home)" value={fmtSigned(pred.expectedMargin)} sub={`80%: ${pred.marginInterval80[0]} … ${pred.marginInterval80[1]}`} />
        <Stat label="Expected total" value={fmtNum(pred.expectedTotal)} sub={`80%: ${pred.totalInterval80[0]} … ${pred.totalInterval80[1]}`} />
        <Stat label="Win probability" value={`${fmtPct(pred.awayWinProbability)} / ${fmtPct(pred.homeWinProbability)}`} sub={`${away.abbreviation} / ${home.abbreviation}`} />
        <Stat label={`Push prob @ ${fmtLine(spread?.line)}`} value={fmtPct(pushAtSpread)} />
        <Stat
          label="Model vs market"
          value={fmtSigned(spread?.line !== undefined ? -pred.expectedMargin - spread.line : 0)}
          sub="model spread − consensus spread"
          tone="accent"
        />
      </section>

      {/* Research predictions from the external analytical engine (opt-in). */}
      <ResearchPanel gameId={game.id} />

      {/* Distributions */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card className="p-3">
          <SectionLabel>Scoring-margin distribution (home − away)</SectionLabel>
          <DistributionChart
            dist={mDist}
            label="Scoring margin distribution"
            markLine={spread?.line !== undefined ? -spread.line : undefined}
            textSummary={`Expected home margin ${fmtSigned(pred.expectedMargin)}; 80% interval ${pred.marginInterval80[0]} to ${pred.marginInterval80[1]}; amber line marks the market-implied margin.`}
          />
        </Card>
        <Card className="p-3">
          <SectionLabel>Total-points distribution</SectionLabel>
          <DistributionChart
            dist={tDist}
            tone="#8b93d6"
            label="Total points distribution"
            markLine={total?.line}
            textSummary={`Expected total ${fmtNum(pred.expectedTotal)}; 80% interval ${pred.totalInterval80[0]} to ${pred.totalInterval80[1]}; amber line marks the consensus total.`}
          />
        </Card>
        <Card className="p-3">
          <SectionLabel>Home cover probability by spread</SectionLabel>
          <CoverByLineChart
            points={coverPoints}
            currentLine={spread?.line}
            label="Cover probability by spread line"
            textSummary={`At the current line ${fmtLine(spread?.line)}, home cover probability is ${fmtPct(spreadOutcomeProbabilities(mDist, spreadLine).cover)}.`}
          />
        </Card>
        <Card className="p-3">
          <SectionLabel>Over probability by total</SectionLabel>
          <CoverByLineChart
            points={overPoints}
            currentLine={total?.line}
            label="Over probability by total line"
            textSummary={`At the current total ${total?.line ?? '—'}, over probability is ${fmtPct(totalOutcomeProbabilities(tDist, total?.line ?? 45).over)}.`}
          />
        </Card>
      </div>

      {/* Key numbers + components */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader title="Probability around key football numbers" />
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <caption className="sr-only">Probability mass on key margins</caption>
              <thead><tr><Th>Key number</Th><Th>{home.abbreviation} wins by exactly</Th><Th>{away.abbreviation} wins by exactly</Th></tr></thead>
              <tbody>
                {keyNumbers.map((k) => (
                  <tr key={k.k}>
                    <Td><Mono>{k.k}</Mono></Td>
                    <Td><Mono>{fmtPct(k.home)}</Mono></Td>
                    <Td><Mono>{fmtPct(k.away)}</Mono></Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card>
          <CardHeader title="Model-component comparison" hint="Weight 0 components are placeholders excluded from the ensemble" />
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <caption className="sr-only">Per-component expected margin and win probability</caption>
              <thead><tr><Th>Component</Th><Th>Home win %</Th><Th>Margin</Th><Th>Total</Th><Th>Weight</Th><Th>Status</Th></tr></thead>
              <tbody>
                {components.map((c) => (
                  <tr key={c.id}>
                    <Td className="text-model">{c.componentName}</Td>
                    <Td><Mono>{fmtPct(c.homeWinProbability)}</Mono></Td>
                    <Td><Mono>{fmtSigned(c.expectedMargin)}</Mono></Td>
                    <Td><Mono>{fmtNum(c.expectedTotal)}</Mono></Td>
                    <Td><Mono>{c.weight.toFixed(2)}</Mono></Td>
                    <Td>{c.isPlaceholder ? <Pill tone="warn">PLACEHOLDER</Pill> : <Pill tone="ok">demo-active</Pill>}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {/* Bet card */}
      {rec ? <BetCard ds={ds} rec={rec} /> : null}

      {/* Matchup analysis */}
      <Card>
        <CardHeader title="Matchup analysis" hint="Raw data vs model-derived metrics; every factor notes whether the market likely already prices it" />
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <caption className="sr-only">Structured matchup factor comparison</caption>
            <thead>
              <tr>
                <Th>Factor</Th><Th>Raw data</Th><Th>Model metric</Th><Th>Estimated effect</Th>
                <Th>Favors</Th><Th>Confidence</Th><Th>Source @ (UTC)</Th><Th>Priced in?</Th>
              </tr>
            </thead>
            <tbody>
              {factors.map((f) => (
                <tr key={f.factor} className="hover:bg-panel-raised/60">
                  <Td className="font-medium">{f.factor}</Td>
                  <Td className="max-w-64 whitespace-normal text-ink-muted">{f.rawData}</Td>
                  <Td className="max-w-56 whitespace-normal text-model">{f.modelMetric}</Td>
                  <Td><Mono>{f.estimatedEffect}</Mono></Td>
                  <Td>{f.favors === 'NEUTRAL' ? <span className="text-ink-faint">—</span> : f.favors === 'HOME' ? home.abbreviation : away.abbreviation}</Td>
                  <Td>{f.confidence}</Td>
                  <Td><Mono className="text-ink-faint">{fmtUtc(f.sourceTimestamp)}</Mono></Td>
                  <Td>{f.likelyPricedIntoMarket ? <span className="text-ink-muted">likely</span> : <span className="text-accent">possibly not</span>}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="px-3 py-2 text-[11px] text-ink-faint">
          Matchup metrics are demonstration values generated deterministically for this demo slate.
        </p>
      </Card>

      {/* Scenario analysis */}
      <Card>
        <CardHeader
          title="Scenario analysis — EXPLORATORY"
          hint="Scenarios never overwrite the official production prediction"
          right={<Pill tone="warn">EXPLORATORY — NOT PRODUCTION</Pill>}
        />
        <div className="grid grid-cols-1 gap-4 p-4 lg:grid-cols-3">
          <fieldset className="space-y-2">
            <legend className="mb-1 text-xs font-medium text-ink-muted">Assumptions</legend>
            <label className="flex items-center gap-2 text-xs text-ink">
              <input type="checkbox" checked={qbOut} onChange={(e) => { setQbOut(e.target.checked); if (e.target.checked) setQbRestricted(false); }} className="accent-[#37b8c8]" />
              Starting quarterback inactive (−{fmtNum(qbImpact)} pts)
            </label>
            <label className="flex items-center gap-2 text-xs text-ink">
              <input type="checkbox" checked={qbRestricted} onChange={(e) => { setQbRestricted(e.target.checked); if (e.target.checked) setQbOut(false); }} className="accent-[#37b8c8]" />
              Quarterback active but restricted
            </label>
            {(game.roofStatus === 'RETRACTABLE_OPEN' || game.roofStatus === 'RETRACTABLE_CLOSED') && (
              <label className="flex items-center gap-2 text-xs text-ink">
                Roof:
                <select
                  value={roofClosed === null ? 'asis' : roofClosed ? 'closed' : 'open'}
                  onChange={(e) => setRoofClosed(e.target.value === 'asis' ? null : e.target.value === 'closed')}
                  className="rounded border border-edge bg-panel-raised px-1.5 py-1 text-[11px]"
                >
                  <option value="asis">as scheduled</option>
                  <option value="open">open</option>
                  <option value="closed">closed</option>
                </select>
              </label>
            )}
            <label className="block text-xs text-ink">
              Wind delta: <Mono>{fmtSigned(windDelta, 0)} mph</Mono>
              <input
                type="range" min={-10} max={15} step={1} value={windDelta}
                onChange={(e) => setWindDelta(Number(e.target.value))}
                className="mt-1 w-full accent-[#37b8c8]" aria-label="Wind speed delta in miles per hour"
              />
            </label>
            <label className="block text-xs text-ink">
              Key-skill snap-share delta: <Mono>{fmtSigned(snapShareDelta, 0)}%</Mono>
              <input
                type="range" min={-30} max={30} step={5} value={snapShareDelta}
                onChange={(e) => setSnapShareDelta(Number(e.target.value))}
                className="mt-1 w-full accent-[#37b8c8]" aria-label="Snap share delta percent"
              />
            </label>
          </fieldset>
          <div className="lg:col-span-2">
            <div className="mb-2 grid grid-cols-3 gap-2">
              <Stat label="Scenario margin" value={fmtSigned(Math.round(scenarioMargin * 10) / 10)} sub={`official ${fmtSigned(pred.expectedMargin)}`} tone={scenarioActive ? 'warning' : 'default'} />
              <Stat label="Scenario total" value={fmtNum(scenarioTotal)} sub={`official ${fmtNum(pred.expectedTotal)}`} tone={scenarioActive ? 'warning' : 'default'} />
              <Stat label="Scenario home win %" value={fmtPct(scenarioHomeWp)} sub={`official ${fmtPct(pred.homeWinProbability)}`} tone={scenarioActive ? 'warning' : 'default'} />
            </div>
            {scenarioActive ? (
              <DistributionChart
                dist={sDist}
                tone="#d9a13b"
                label="Exploratory scenario margin distribution"
                markLine={spread?.line !== undefined ? -spread.line : undefined}
                textSummary={`Exploratory scenario only: margin ${fmtSigned(scenarioMargin)} vs official ${fmtSigned(pred.expectedMargin)}; scenario 80% interval ${centralInterval(sDist, 0.8)[0]} to ${centralInterval(sDist, 0.8)[1]}. Total ${fmtNum(scenarioTotal)} (dist ±${fmtNum(sTotalDist.length ? pred.totalStd : 0)}).`}
              />
            ) : (
              <p className="text-xs text-ink-faint">Adjust an assumption to explore a non-production scenario.</p>
            )}
          </div>
        </div>
      </Card>

      {/* Prediction vintages */}
      <Card>
        <CardHeader title="Prediction vintages" hint="Immutable history — later predictions never overwrite earlier ones" />
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <caption className="sr-only">Prediction history by vintage</caption>
            <thead>
              <tr>
                <Th>Vintage</Th><Th>As-of (UTC)</Th><Th>Home win %</Th><Th>Margin</Th><Th>Total</Th>
                <Th>Margin 80%</Th><Th>Data</Th><Th>Δ vs prior</Th><Th>Why it changed</Th>
              </tr>
            </thead>
            <tbody>
              {allVintages.map((p, i) => {
                const prev = i > 0 ? allVintages[i - 1] : undefined;
                const dm = prev ? p.expectedMargin - prev.expectedMargin : 0;
                return (
                  <tr key={p.id} className={p.isOfficial ? 'bg-accent/5' : undefined}>
                    <Td className="font-medium">{p.vintage}{p.isOfficial ? ' · OFFICIAL' : ''}</Td>
                    <Td><Mono>{fmtUtc(p.asOfAt)}</Mono></Td>
                    <Td><Mono>{fmtPct(p.homeWinProbability)}</Mono></Td>
                    <Td><Mono>{fmtSigned(p.expectedMargin)}</Mono></Td>
                    <Td><Mono>{fmtNum(p.expectedTotal)}</Mono></Td>
                    <Td><Mono>{p.marginInterval80[0]} … {p.marginInterval80[1]}</Mono></Td>
                    <Td><Mono>{fmtPct(p.dataCompletenessScore, 0)}</Mono></Td>
                    <Td><Mono className={Math.abs(dm) > 0.5 ? 'text-warn' : 'text-ink-faint'}>{prev ? fmtSigned(dm) : '—'}</Mono></Td>
                    <Td className="max-w-72 whitespace-normal text-[11px] text-ink-muted">
                      {prev
                        ? Math.abs(dm) > 0.01
                          ? 'Practice reports, availability probabilities, and market movement observed before this cutoff'
                          : 'No material data change before this cutoff'
                        : 'Baseline opening estimate from ratings + market prior'}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
