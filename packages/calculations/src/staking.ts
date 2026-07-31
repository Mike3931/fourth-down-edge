import type { RiskControls, StakeBreakdown } from '@fde/shared-types';
import { fullKelly } from './kelly';

export interface StakingInputs {
  winProbability: number;
  american: number;
  pushProbability: number;
  bankroll: number;
  riskControls: RiskControls;
  /** 0..1 haircut multipliers (1 = no haircut). */
  uncertaintyMultiplier: number;
  dataQualityMultiplier: number;
  calibrationMultiplier: number;
  /** Existing exposure as fraction of bankroll. */
  existingGameExposurePct: number;
  existingTeamExposurePct: number;
  existingClusterExposurePct: number;
  existingWeeklyOpenStakePct: number;
}

/**
 * Computes the transparent stake pipeline:
 * full Kelly -> fractional Kelly -> uncertainty haircut -> data-quality
 * haircut -> calibration haircut -> per-bet cap -> per-game cap -> team cap
 * -> correlated-cluster cap -> weekly cap. Reports which constraint bound.
 */
export function computeStake(inputs: StakingInputs): StakeBreakdown {
  const {
    winProbability, american, pushProbability, bankroll, riskControls: rc,
  } = inputs;

  for (const [label, m] of [
    ['uncertaintyMultiplier', inputs.uncertaintyMultiplier],
    ['dataQualityMultiplier', inputs.dataQualityMultiplier],
    ['calibrationMultiplier', inputs.calibrationMultiplier],
  ] as const) {
    if (!(m >= 0 && m <= 1)) throw new RangeError(`${label} must be in [0, 1], got ${m}`);
  }
  if (!(bankroll >= 0)) throw new RangeError('Bankroll must be non-negative.');

  const fullKellyPct = fullKelly(winProbability, american, pushProbability);
  const quarterKellyPct = fullKellyPct * rc.kellyFraction;
  const afterUncertaintyHaircutPct = quarterKellyPct * inputs.uncertaintyMultiplier;
  const afterDataQualityHaircutPct = afterUncertaintyHaircutPct * inputs.dataQualityMultiplier;
  const afterCalibrationHaircutPct = afterDataQualityHaircutPct * inputs.calibrationMultiplier;

  // Remaining room under each portfolio cap (never negative).
  const room = (cap: number, existing: number) => Math.max(0, cap - existing);

  const candidates: Array<{ pct: number; label: string }> = [
    { pct: afterCalibrationHaircutPct, label: 'Risk-adjusted Kelly' },
    { pct: rc.maxPerBetPctOfBankroll, label: 'Per-bet cap' },
    { pct: room(rc.maxPerGamePct, inputs.existingGameExposurePct), label: 'Same-game cap' },
    { pct: room(rc.maxPerTeamWeeklyPct, inputs.existingTeamExposurePct), label: 'Team weekly cap' },
    { pct: room(rc.maxCorrelatedClusterPct, inputs.existingClusterExposurePct), label: 'Correlated-cluster cap' },
    { pct: room(rc.maxWeeklyOpenStakePct, inputs.existingWeeklyOpenStakePct), label: 'Weekly open-stake cap' },
  ];

  let finalStakePct = candidates[0]!.pct;
  let bindingConstraint = candidates[0]!.label;
  for (const c of candidates) {
    if (c.pct < finalStakePct) {
      finalStakePct = c.pct;
      bindingConstraint = c.label;
    }
  }
  finalStakePct = Math.max(0, finalStakePct);
  if (finalStakePct === 0 && afterCalibrationHaircutPct === 0) {
    bindingConstraint = 'No positive Kelly stake';
  }

  const finalStakeAmount = roundCents(bankroll * finalStakePct);

  return {
    fullKellyPct,
    quarterKellyPct,
    afterUncertaintyHaircutPct,
    afterDataQualityHaircutPct,
    afterCalibrationHaircutPct,
    perBetCapPct: rc.maxPerBetPctOfBankroll,
    finalStakePct,
    finalStakeAmount,
    bindingConstraint,
  };
}

export function roundCents(x: number): number {
  return Math.round(x * 100) / 100;
}
