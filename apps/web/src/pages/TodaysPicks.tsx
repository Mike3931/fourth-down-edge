import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button, Card, ErrorState, LoadingState, cn } from '@fde/ui';
import type { Recommendation } from '@fde/shared-types';
import { useDataset, useRecommendations } from '../lib/api';
import { useStore } from '../lib/store';
import { fmtKickoff, fmtMarketLine, fmtMoney, fmtOdds } from '../lib/format';
import { gameById, gameLabelLong, teamById } from '../lib/joins';

/**
 * The simple front door of the app: "here are today's best bets," full
 * stop. Every number shown is read from the same deterministic Recommendation
 * objects the research terminal uses underneath (packages/api-client) — this
 * page adds no new logic, only a plainer presentation of it. Anyone who wants
 * the full math, factor-by-factor breakdown, or edit controls is one click
 * away via "Full analysis."
 */
export default function TodaysPicks() {
  const { data: ds, isLoading: dsLoading, error: dsError } = useDataset();
  const { data: recs, isLoading: recsLoading, error: recsError } = useRecommendations();
  const store = useStore();
  const [showWatch, setShowWatch] = useState(false);
  const [placedRecs, setPlacedRecs] = useState<Map<string, Recommendation>>(new Map());
  const [placeError, setPlaceError] = useState<string | null>(null);

  const { picks, watching } = useMemo(() => {
    if (!recs) return { picks: [] as Recommendation[], watching: [] as Recommendation[] };
    const bySortEdge = (a: Recommendation, b: Recommendation) => b.edge - a.edge;
    const live = recs.filter((r) => r.status === 'BET');
    const liveIds = new Set(live.map((r) => r.id));
    // Recording a pick changes its own exposure, which can drop it out of the
    // live BET list on the next evaluation (no double-dipping on one game).
    // Keep it visible in its recorded state instead of letting it vanish out
    // from under the user right after they act on it.
    const recorded = [...placedRecs.values()].filter((r) => !liveIds.has(r.id));
    return {
      picks: [...live, ...recorded].sort(bySortEdge).slice(0, 10),
      watching: recs.filter((r) => r.status === 'WATCH').sort(bySortEdge),
    };
  }, [recs, placedRecs]);

  // Recorded state above only survives within this visit. Across visits the
  // ledger is the durable truth: surface open bets on this week's games so a
  // pick the user already acted on never looks like it silently disappeared.
  const openThisWeek = useMemo(() => {
    if (!ds) return 0;
    const weekGameIds = new Set(ds.games.map((g) => g.id));
    return store.openBets.filter((b) => weekGameIds.has(b.gameId)).length;
  }, [ds, store.openBets]);

  if (dsLoading || recsLoading) return <LoadingState label="Finding today's best bets…" />;
  if (dsError || recsError || !ds || !recs) return <ErrorState title="Couldn't load today's picks" />;

  function onRecord(rec: Recommendation) {
    if (!rec.stake) return;
    setPlaceError(null);
    const res = store.placePaperBet(rec, rec.stake.finalStakeAmount, ds!.demoNow, 'PAPER');
    if (res.error) setPlaceError(res.error);
    else setPlacedRecs((prev) => new Map(prev).set(rec.id, rec));
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6 pb-8">
      <div className="text-center">
        <h1 className="text-2xl font-semibold text-ink">Today's Top Picks</h1>
        <p className="mt-1.5 text-sm text-ink-muted">
          {picks.length > 0
            ? `${picks.length} strong ${picks.length === 1 ? 'opportunity' : 'opportunities'} found for Week ${ds.week}.`
            : `No strong opportunities right now — the system is being conservative on purpose.`}
        </p>
        {openThisWeek > 0 ? (
          <p className="mt-1 text-xs text-ink-faint">
            You've already recorded {openThisWeek} {openThisWeek === 1 ? 'pick' : 'picks'} this week —{' '}
            <Link
              to="/portfolio"
              className="underline decoration-dotted underline-offset-2 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            >
              track them in Bet Portfolio
            </Link>
            .
          </p>
        ) : null}
      </div>

      {placeError ? (
        <p role="alert" className="rounded border border-bad/40 bg-bad/10 px-3 py-2 text-center text-xs text-bad">
          {placeError}
        </p>
      ) : null}

      {picks.length === 0 ? (
        <Card className="p-8 text-center">
          <p className="text-sm text-ink-muted">
            {openThisWeek > 0
              ? 'Nothing further to add: the picks that qualified are already recorded, and the system won’t stack more exposure on top of them.'
              : 'The system only recommends a bet when the price, the data, and the model all line up. That didn’t happen for any game this week — passing is the normal, expected outcome, not a malfunction.'}
          </p>
          {watching.length > 0 ? (
            <p className="mt-3 text-sm text-ink-muted">
              {watching.length} {watching.length === 1 ? 'game is' : 'games are'} close — worth a look below.
            </p>
          ) : null}
          <Link
            to="/slate"
            className="mt-4 inline-block rounded border border-edge px-4 py-2 text-xs font-medium text-ink-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            See the full slate →
          </Link>
        </Card>
      ) : (
        <div className="space-y-4">
          {picks.map((rec, i) => (
            <PickCard
              key={rec.id}
              rank={i + 1}
              rec={rec}
              ds={ds}
              oddsFormat={store.settings.oddsFormat}
              timezone={store.settings.timezone}
              placed={placedRecs.has(rec.id)}
              onRecord={() => onRecord(rec)}
            />
          ))}
        </div>
      )}

      {watching.length > 0 ? (
        <div className="pt-2">
          <button
            onClick={() => setShowWatch((v) => !v)}
            className="mx-auto flex items-center gap-1.5 rounded px-3 py-1.5 text-xs text-ink-faint hover:text-ink-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            aria-expanded={showWatch}
          >
            <span aria-hidden="true">{showWatch ? '▾' : '▸'}</span>
            {watching.length} more {watching.length === 1 ? 'game' : 'games'} worth watching (not quite a pick yet)
          </button>
          {showWatch ? (
            <div className="mt-3 space-y-2">
              {watching.map((rec) => (
                <WatchRow key={rec.id} rec={rec} ds={ds} oddsFormat={store.settings.oddsFormat} />
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <p className="pt-4 text-center text-[11px] leading-relaxed text-ink-faint">
        Paper picks only — nothing here places a real wager. Every pick links to the full research behind
        it. Demonstration data — not for real-money decisions.
      </p>
    </div>
  );
}

/** Plain-language description of the selection, shared by pick cards and watch rows. */
function pickLabelFor(ds: NonNullable<ReturnType<typeof useDataset>['data']>, rec: Recommendation): string {
  const game = gameById(ds, rec.gameId)!;
  const home = teamById(ds, game.homeTeamId);
  const away = teamById(ds, game.awayTeamId);
  const pickedTeam = rec.selection === 'HOME' ? home.name : rec.selection === 'AWAY' ? away.name : undefined;
  return rec.market === 'MONEYLINE'
    ? `${pickedTeam} to win`
    : rec.market === 'SPREAD'
      ? `${pickedTeam} ${fmtMarketLine(rec.market, rec.line)}`
      : `${rec.selection === 'OVER' ? 'Over' : 'Under'} ${rec.line}`;
}

function ConfidenceMeter({ confidence }: { confidence: Recommendation['confidence'] }) {
  const level = confidence === 'HIGH' ? 3 : confidence === 'MEDIUM' ? 2 : 1;
  const label = confidence === 'HIGH' ? 'Strong pick' : confidence === 'MEDIUM' ? 'Solid pick' : 'Modest edge';
  return (
    <div className="flex items-center gap-1.5">
      <div className="flex gap-0.5" role="img" aria-label={`Confidence: ${label}`}>
        {[1, 2, 3].map((n) => (
          <span
            key={n}
            aria-hidden="true"
            className={cn('h-1.5 w-4 rounded-full', n <= level ? 'bg-ok' : 'bg-edge')}
          />
        ))}
      </div>
      <span className="text-xs font-medium text-ink-muted">{label}</span>
    </div>
  );
}

function PickCard({
  rank, rec, ds, oddsFormat, timezone, placed, onRecord,
}: {
  rank: number;
  rec: Recommendation;
  ds: NonNullable<ReturnType<typeof useDataset>['data']>;
  oddsFormat: 'AMERICAN' | 'DECIMAL';
  timezone: string;
  placed: boolean;
  onRecord: () => void;
}) {
  const game = gameById(ds, rec.gameId)!;
  const pickLabel = pickLabelFor(ds, rec);
  const why = rec.supportingFactors[0];
  // Most specific opposing factor first (the generic demo disclaimer is pushed
  // last by the evaluator, so it only surfaces here when nothing else opposes).
  const caution = rec.opposingFactors[0];

  return (
    <Card className="overflow-hidden">
      <div className="flex items-start gap-3 p-5">
        <div
          className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-accent/15 font-mono text-xs font-bold text-accent"
          aria-hidden="true"
        >
          {rank}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs text-ink-faint">{gameLabelLong(ds, rec.gameId)} · {fmtKickoff(game.kickoffUtc, timezone)}</p>
          <p className="mt-1 text-xl font-semibold text-ink text-balance">{pickLabel}</p>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5">
            <ConfidenceMeter confidence={rec.confidence} />
            <span className="font-mono text-sm text-ink-muted">{fmtOdds(rec.american, oddsFormat)}</span>
          </div>
          {why ? <p className="mt-2.5 text-sm text-ink-muted">{why}</p> : null}
          {caution ? (
            <p className="mt-1.5 text-xs text-warn/90">
              <span className="font-medium">Caution:</span> {caution}
            </p>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center gap-3">
            {placed ? (
              <span className="rounded border border-ok/40 bg-ok/10 px-3 py-1.5 text-xs font-medium text-ok">
                ✓ Recorded
              </span>
            ) : rec.stake && rec.stake.finalStakeAmount > 0 ? (
              <Button variant="primary" onClick={onRecord}>
                Record this pick · {fmtMoney(rec.stake.finalStakeAmount)}
              </Button>
            ) : null}
            <Link
              to={`/game/${rec.gameId}`}
              className="text-xs text-ink-faint underline decoration-dotted underline-offset-2 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            >
              Full analysis →
            </Link>
          </div>
        </div>
      </div>
    </Card>
  );
}

function WatchRow({
  rec, ds, oddsFormat,
}: {
  rec: Recommendation;
  ds: NonNullable<ReturnType<typeof useDataset>['data']>;
  oddsFormat: 'AMERICAN' | 'DECIMAL';
}) {
  const pickLabel = pickLabelFor(ds, rec);

  return (
    <Link
      to={`/game/${rec.gameId}`}
      className="flex items-center justify-between gap-3 rounded border border-edge bg-panel px-4 py-3 text-sm hover:border-ink-faint focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
    >
      <div className="min-w-0">
        <p className="truncate text-ink">{pickLabel}</p>
        <p className="truncate text-xs text-ink-faint">{gameLabelLong(ds, rec.gameId)}</p>
      </div>
      <span className="shrink-0 font-mono text-xs text-ink-muted">{fmtOdds(rec.american, oddsFormat)}</span>
    </Link>
  );
}
