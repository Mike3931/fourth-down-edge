import { useState } from 'react';
import { Button, Card, CardHeader, Mono, Pill, RecBadge, SectionLabel, Term, cn } from '@fde/ui';
import { americanToDecimal, americanToImpliedProbability } from '@fde/calculations';
import type { Recommendation } from '@fde/shared-types';
import type { DemoDataset } from '@fde/api-client';
import { gameLabel, gameLabelLong, modelVersionLabel } from '../lib/joins';
import { fmtAgo, fmtMarketLine, fmtMoney, fmtOdds, fmtPct, fmtSignedPct, fmtUtc } from '../lib/format';
import { useStore } from '../lib/store';

/**
 * Reusable Bet Card: the complete decision record for one opportunity.
 * Every number is produced by deterministic code in @fde/calculations —
 * never by a language model.
 */
export default function BetCard({ ds, rec }: { ds: DemoDataset; rec: Recommendation }) {
  const store = useStore();
  const [placed, setPlaced] = useState<string | null>(null);
  const [placeError, setPlaceError] = useState<string | null>(null);

  const decimal = americanToDecimal(rec.american);
  const rawImplied = americanToImpliedProbability(rec.american);
  const manual = rec.manualPriceId
    ? ds.manualPrices.find((m) => m.id === rec.manualPriceId)
    : undefined;
  const pred = ds.predictions.find((p) => p.id === rec.predictionId);
  const gameStake = store.gameExposure(rec.gameId);
  const bankroll = store.ledger?.bankrollBalance ?? 0;

  const canPlacePaper = rec.status === 'BET' && rec.stake && rec.stake.finalStakeAmount > 0;

  function onRecordPaperBet() {
    if (!rec.stake) return;
    const res = store.placePaperBet(rec, rec.stake.finalStakeAmount, ds.demoNow, 'PAPER');
    if (res.error) setPlaceError(res.error);
    else setPlaced(`Paper bet recorded: ${fmtMoney(rec.stake.finalStakeAmount)} at ${fmtOdds(rec.american, store.settings.oddsFormat)}`);
  }

  const num = (v: string, cls?: string) => <Mono className={cn('text-ink', cls)}>{v}</Mono>;

  return (
    <Card>
      <CardHeader
        title={
          <span className="normal-case tracking-normal text-ink">
            {gameLabel(ds, rec.gameId)} · {rec.market} · {rec.selection}
            {rec.line !== undefined ? ` ${fmtMarketLine(rec.market, rec.line)}` : ''}
          </span>
        }
        hint={gameLabelLong(ds, rec.gameId)}
        right={<RecBadge status={rec.status} />}
      />
      <div className="grid grid-cols-1 gap-4 p-4 lg:grid-cols-3">
        {/* Pricing column */}
        <div>
          <SectionLabel>Price &amp; probabilities</SectionLabel>
          <dl className="space-y-1 text-xs">
            <Row k="Entered price" v={num(fmtOdds(rec.american, store.settings.oddsFormat))} />
            <Row k="Line" v={num(fmtMarketLine(rec.market, rec.line))} />
            <Row k="Decimal odds" v={num(decimal.toFixed(3))} />
            <Row k={<Term term="Raw implied prob" def="Probability implied by the price including the bookmaker margin (vig)." />} v={num(fmtPct(rawImplied))} />
            <Row k={<Term term="No-vig market prob" def="Market probability after proportionally removing the bookmaker margin." />} v={num(fmtPct(rec.marketNoVigProbability))} />
            <Row k="Model probability" v={num(fmtPct(rec.modelProbability))} />
            <Row k={<Term term="Conservative prob" def="Model probability shrunk toward the market no-vig probability before any stake is computed." />} v={num(fmtPct(rec.conservativeProbability))} />
            <Row k="Break-even prob" v={num(fmtPct(rec.breakEvenProbability))} />
            <Row k="Push probability" v={num(fmtPct(rec.pushProbability))} />
            <Row k="Fair American odds" v={num(fmtOdds(rec.fairAmerican, store.settings.oddsFormat))} />
            <Row k="Estimated edge" v={num(fmtSignedPct(rec.edge), rec.edge > 0 ? 'text-ok' : 'text-ink-muted')} />
            <Row k="EV per $1" v={num(`${rec.evPerDollar >= 0 ? '+' : ''}${rec.evPerDollar.toFixed(4)}`, rec.evPerDollar > 0 ? 'text-ok' : 'text-ink-muted')} />
            <Row k="Uncertainty interval" v={num(pred ? `margin ${pred.marginInterval80[0]}…${pred.marginInterval80[1]} (80%)` : '—')} />
          </dl>
        </div>

        {/* Staking column */}
        <div>
          <SectionLabel>Stake pipeline (transparent)</SectionLabel>
          {rec.stake ? (
            <dl className="space-y-1 text-xs">
              <Row k={<Term term="Full Kelly" def="Bankroll fraction maximizing long-run log growth if the probability estimate were exactly correct. Never bet full Kelly." />} v={num(fmtPct(rec.stake.fullKellyPct, 2))} />
              <Row k="Quarter Kelly" v={num(fmtPct(rec.stake.quarterKellyPct, 2))} />
              <Row k="− uncertainty haircut" v={num(fmtPct(rec.stake.afterUncertaintyHaircutPct, 2))} />
              <Row k="− data-quality haircut" v={num(fmtPct(rec.stake.afterDataQualityHaircutPct, 2))} />
              <Row k="− calibration haircut" v={num(fmtPct(rec.stake.afterCalibrationHaircutPct, 2))} />
              <Row k="Per-bet cap" v={num(fmtPct(rec.stake.perBetCapPct, 2))} />
              <Row k="Final capped stake" v={num(`${fmtPct(rec.stake.finalStakePct, 2)} = ${fmtMoney(rec.stake.finalStakeAmount)}`, 'text-accent')} />
              <Row k="Binding constraint" v={<span className="text-warn">{rec.stake.bindingConstraint}</span>} />
            </dl>
          ) : (
            <p className="text-xs text-ink-faint">No stake — status is {rec.status}.</p>
          )}
          <div className="mt-3 space-y-1 text-xs">
            <Row k="Current bankroll" v={num(fmtMoney(bankroll))} />
            <Row k="Game exposure" v={num(fmtMoney(gameStake))} />
            <Row k="Weekly exposure" v={num(fmtPct(store.weeklyExposurePct))} />
            {rec.market !== 'MONEYLINE' && store.openBets.some((b) => b.gameId === rec.gameId && b.market !== rec.market) ? (
              <p className="text-warn">⚠ Correlation warning: open position in another market on this game.</p>
            ) : null}
          </div>
          <div className="mt-3 space-y-1 text-[11px] text-ink-faint">
            <p>Data completeness: <Mono>{pred ? fmtPct(pred.dataCompletenessScore, 0) : '—'}</Mono></p>
            <p>Model version: <Mono className="text-model">{modelVersionLabel(ds, pred?.modelVersionId)} (demo)</Mono></p>
            <p>Prediction as-of: <Mono>{pred ? fmtUtc(pred.asOfAt) : '—'}</Mono></p>
            <p>
              Price confirmed:{' '}
              <Mono>{manual ? `${fmtUtc(manual.enteredAt)} (${fmtAgo(manual.enteredAt, ds.demoNow)})` : 'no manual price on file'}</Mono>
            </p>
          </div>
        </div>

        {/* Reasoning column */}
        <div>
          <SectionLabel>Decision record</SectionLabel>
          <div className="space-y-2 text-xs">
            {rec.supportingFactors.length > 0 && (
              <div>
                <p className="font-medium text-ok">Supporting factors</p>
                <ul className="mt-0.5 list-inside list-disc space-y-0.5 text-ink-muted">
                  {rec.supportingFactors.map((f) => <li key={f}>{f}</li>)}
                </ul>
              </div>
            )}
            {rec.opposingFactors.length > 0 && (
              <div>
                <p className="font-medium text-warn">Opposing factors</p>
                <ul className="mt-0.5 list-inside list-disc space-y-0.5 text-ink-muted">
                  {rec.opposingFactors.map((f) => <li key={f}>{f}</li>)}
                </ul>
              </div>
            )}
            {rec.reasonsToPass.length > 0 && (
              <div>
                <p className="font-medium text-ink-muted">Reasons to pass</p>
                <ul className="mt-0.5 list-inside list-disc space-y-0.5 text-ink-muted">
                  {rec.reasonsToPass.map((f) => <li key={f}>{f}</li>)}
                </ul>
              </div>
            )}
            <div>
              <p className="font-medium text-bad">What would invalidate this</p>
              <ul className="mt-0.5 list-inside list-disc space-y-0.5 text-ink-muted">
                {rec.invalidationConditions.map((f) => <li key={f}>{f}</li>)}
              </ul>
            </div>
            <div className="space-y-1">
              <p>
                Target price for BET:{' '}
                {num(rec.targetPrice !== undefined ? fmtOdds(rec.targetPrice, store.settings.oddsFormat) : '—')}
              </p>
              <p>
                Invalidation price:{' '}
                {num(rec.invalidationPrice !== undefined ? fmtOdds(rec.invalidationPrice, store.settings.oddsFormat) : '—')}
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5 pt-1">
              <Pill tone="model">confidence: {rec.confidence}</Pill>
              <Pill tone="neutral" title={fmtUtc(rec.createdAt)}>evaluated {fmtAgo(rec.createdAt, ds.demoNow)}</Pill>
            </div>
          </div>

          {canPlacePaper ? (
            <div className="mt-3 border-t border-edge pt-3">
              {placed ? (
                <p className="text-xs text-ok">{placed}</p>
              ) : (
                <>
                  <Button variant="primary" onClick={onRecordPaperBet}>
                    Record paper bet · {fmtMoney(rec.stake!.finalStakeAmount)}
                  </Button>
                  <p className="mt-1.5 text-[11px] text-ink-faint">
                    Records a PAPER ledger entry only. Nothing is transmitted to any sportsbook.
                  </p>
                </>
              )}
              {placeError ? <p className="mt-1 text-xs text-bad" role="alert">{placeError}</p> : null}
            </div>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

function Row({ k, v }: { k: React.ReactNode; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-ink-faint">{k}</dt>
      <dd className="text-right">{v}</dd>
    </div>
  );
}
