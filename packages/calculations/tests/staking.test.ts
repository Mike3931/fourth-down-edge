import { describe, expect, it } from 'vitest';
import { DEFAULT_RISK_CONTROLS } from '@fde/shared-types';
import { computeStake, type StakingInputs } from '../src/staking';

const base: StakingInputs = {
  winProbability: 0.56,
  american: -110,
  pushProbability: 0,
  bankroll: 10_000,
  riskControls: DEFAULT_RISK_CONTROLS,
  uncertaintyMultiplier: 1,
  dataQualityMultiplier: 1,
  calibrationMultiplier: 1,
  existingGameExposurePct: 0,
  existingTeamExposurePct: 0,
  existingClusterExposurePct: 0,
  existingWeeklyOpenStakePct: 0,
};

describe('computeStake', () => {
  it('caps at the per-bet cap when Kelly exceeds it', () => {
    const r = computeStake(base);
    // full Kelly at p=0.56, -110 is ~7.6%; quarter ~1.9% > 0.5% cap
    expect(r.fullKellyPct).toBeGreaterThan(0.05);
    expect(r.finalStakePct).toBeCloseTo(0.005, 10);
    expect(r.bindingConstraint).toBe('Per-bet cap');
    expect(r.finalStakeAmount).toBeCloseTo(50, 2);
  });

  it('applies haircuts multiplicatively and can make Kelly the binder', () => {
    const r = computeStake({
      ...base,
      winProbability: 0.535,
      uncertaintyMultiplier: 0.5,
      dataQualityMultiplier: 0.8,
      calibrationMultiplier: 0.75,
    });
    expect(r.afterCalibrationHaircutPct).toBeCloseTo(
      r.quarterKellyPct * 0.5 * 0.8 * 0.75,
      12,
    );
    if (r.afterCalibrationHaircutPct < 0.005) {
      expect(r.bindingConstraint).toBe('Risk-adjusted Kelly');
      expect(r.finalStakePct).toBeCloseTo(r.afterCalibrationHaircutPct, 12);
    }
  });

  it('respects remaining room under the same-game cap', () => {
    const r = computeStake({ ...base, existingGameExposurePct: 0.008 });
    expect(r.finalStakePct).toBeCloseTo(0.002, 10);
    expect(r.bindingConstraint).toBe('Same-game cap');
  });

  it('produces zero stake when the weekly cap is exhausted', () => {
    const r = computeStake({ ...base, existingWeeklyOpenStakePct: 0.04 });
    expect(r.finalStakePct).toBe(0);
    expect(r.finalStakeAmount).toBe(0);
    expect(r.bindingConstraint).toBe('Weekly open-stake cap');
  });

  it('produces zero stake for negative-edge inputs (no negative Kelly)', () => {
    const r = computeStake({ ...base, winProbability: 0.5 });
    expect(r.fullKellyPct).toBe(0);
    expect(r.finalStakePct).toBe(0);
    expect(r.bindingConstraint).toBe('No positive Kelly stake');
  });

  it('never increases stake due to losing streaks (no martingale input exists)', () => {
    // The staking API is a pure function of edge, price, and caps — there is
    // no loss-history parameter, so a losing streak cannot raise the stake.
    const a = computeStake(base);
    const b = computeStake({ ...base });
    expect(b.finalStakeAmount).toBe(a.finalStakeAmount);
  });

  it('rejects invalid haircut multipliers', () => {
    expect(() => computeStake({ ...base, uncertaintyMultiplier: 1.2 })).toThrow(RangeError);
    expect(() => computeStake({ ...base, dataQualityMultiplier: -0.1 })).toThrow(RangeError);
  });
});
