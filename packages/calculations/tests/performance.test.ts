import { describe, expect, it } from 'vitest';
import {
  brierScore,
  calibrationBins,
  calibrationLine,
  closingLineValuePct,
  expectedCalibrationError,
  logLoss,
  maxDrawdown,
} from '../src/performance';

describe('performance metrics', () => {
  it('log loss of certain correct predictions approaches 0', () => {
    expect(logLoss([{ p: 0.999999, outcome: 1 }])).toBeLessThan(0.001);
  });
  it('log loss of p=0.5 is ln 2', () => {
    expect(logLoss([{ p: 0.5, outcome: 1 }, { p: 0.5, outcome: 0 }])).toBeCloseTo(Math.log(2), 10);
  });
  it('brier score of p=0.5 is 0.25', () => {
    expect(brierScore([{ p: 0.5, outcome: 1 }])).toBeCloseTo(0.25, 10);
  });
  it('perfectly calibrated bins have zero ECE', () => {
    const pairs: Array<{ p: number; outcome: 0 | 1 }> = [];
    for (let i = 0; i < 100; i++) pairs.push({ p: 0.75, outcome: i < 75 ? 1 : 0 });
    const bins = calibrationBins(pairs);
    expect(expectedCalibrationError(bins, pairs.length)).toBeCloseTo(0, 10);
  });
  it('calibration line recovers slope 1 for calibrated data', () => {
    const pairs: Array<{ p: number; outcome: 0 | 1 }> = [];
    for (const p of [0.2, 0.4, 0.6, 0.8]) {
      for (let i = 0; i < 100; i++) pairs.push({ p, outcome: i < p * 100 ? 1 : 0 });
    }
    const { slope } = calibrationLine(pairs);
    expect(slope).toBeCloseTo(1, 5);
  });
  it('max drawdown of a monotone curve is 0', () => {
    expect(maxDrawdown([100, 110, 120])).toBe(0);
  });
  it('max drawdown captures the worst peak-to-trough drop', () => {
    expect(maxDrawdown([100, 120, 90, 110])).toBeCloseTo(0.25, 10);
  });
  it('CLV is positive when the close implies a higher probability than paid', () => {
    expect(closingLineValuePct(0.5, 0.55)).toBeCloseTo(5, 10);
    expect(closingLineValuePct(0.55, 0.5)).toBeCloseTo(-5, 10);
  });
});
