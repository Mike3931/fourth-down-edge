/**
 * Turning a STORED market line into something a person can read.
 *
 * The engine stores SPREAD lines HOME-RELATIVE: both rows of a book carry
 * the home team's handicap, because `_write_quote` negates the away point
 * on capture. That convention is correct and deliberate — a single column
 * holding two opposite conventions is what made a consensus average a line
 * against its own mirror, and it cost a day to find.
 *
 * But it is a STORAGE convention, and the screen is not storage. Rendering
 * the stored number beside the label "AWAY" tells the reader that the away
 * team is getting +1.5 when the away team is actually laying -1.5. The
 * table was accurate and still communicated the opposite of the truth,
 * which is the worst failure mode available to a display layer.
 *
 * So the sign is flipped exactly once, here, on the way to the screen, and
 * nowhere else. Nothing below writes, and nothing below is used to price:
 * `homeRelativeLine` remains the number every calculation sees.
 *
 * Totals get the other half of the fix. A total has no direction — 34.5 is
 * 34.5 — and the shared sign-everything formatter was rendering it as
 * "+34.5", which reads as a spread. OVER and UNDER already carry the
 * direction in the selection label.
 */

export type MarketKind = 'SPREAD' | 'TOTAL' | 'MONEYLINE' | string;

/** Sides as the engine records them. */
export type Selection = 'HOME' | 'AWAY' | 'OVER' | 'UNDER' | string;

/**
 * The handicap that belongs to `selection`, given a HOME-RELATIVE stored
 * line. Returns null when the market carries no line (moneyline) or none
 * was captured — callers render that as an em dash, never as 0.
 */
export function lineForSelection(
  market: MarketKind,
  selection: Selection,
  homeRelativeLine: number | null,
): number | null {
  if (homeRelativeLine === null || homeRelativeLine === undefined) return null;
  if (market === 'MONEYLINE') return null;
  if (market !== 'SPREAD') return homeRelativeLine; // totals are not mirrored
  if (selection === 'AWAY') return negateZero(-homeRelativeLine);
  return homeRelativeLine;
}

/**
 * Format a line for display. Spreads are signed because the sign IS the
 * information; totals are not, because they have no direction.
 *
 * A pick'em is "PK", not "+0" or "-0" — a signed zero is the one spread
 * value where the sign is meaningless and looks like a bug.
 */
export function formatLine(market: MarketKind, line: number | null): string {
  if (line === null || line === undefined || Number.isNaN(line)) return '—';
  if (market === 'MONEYLINE') return '—';
  // String(34.5) is "34.5" and String(3.0) is "3", so no trimming is needed.
  if (market !== 'SPREAD') return String(line);
  if (line === 0) return 'PK';
  return line > 0 ? `+${line}` : `-${Math.abs(line)}`;
}

/**
 * The whole cell: the team or side that owns this row, and its number.
 * "ARI +1.5" cannot be misread the way a bare "+1.5" under "AWAY" can.
 */
export function describeQuote(
  market: MarketKind,
  selection: Selection,
  homeRelativeLine: number | null,
  teams: { homeTeamId: string; awayTeamId: string },
): string {
  const shown = formatLine(market, lineForSelection(market, selection, homeRelativeLine));
  if (market === 'SPREAD') {
    const team = selection === 'AWAY' ? teams.awayTeamId : teams.homeTeamId;
    return shown === '—' ? team : `${team} ${shown}`;
  }
  if (market === 'TOTAL') {
    const side = selection === 'UNDER' ? 'Under' : 'Over';
    return shown === '—' ? side : `${side} ${shown}`;
  }
  // Moneyline carries no handicap; the price alone is the quote.
  return selection === 'AWAY' ? teams.awayTeamId : teams.homeTeamId;
}

/**
 * A consensus SPREAD median is home-relative like everything else, so it
 * is stated against the home team by name rather than left ambiguous.
 */
export function describeConsensus(
  market: MarketKind,
  medianLine: number | null,
  teams: { homeTeamId: string; awayTeamId: string },
): string {
  if (medianLine === null || medianLine === undefined) return '—';
  if (market === 'SPREAD') {
    return describeQuote('SPREAD', 'HOME', medianLine, teams);
  }
  if (market === 'TOTAL') return formatLine('TOTAL', medianLine);
  return '—';
}

/** -0 is a real IEEE value and it renders as "-0". Never show it. */
function negateZero(n: number): number {
  return n === 0 ? 0 : n;
}

/**
 * Do repeated evaluations of the same thing actually disagree?
 *
 * A backtest rerun over identical games should reproduce its numbers. When
 * it does not, that is a reproducibility problem worth surfacing — it is
 * how the Model Audit screen revealed it was citing a superseded metric.
 *
 * But "not identical" is the wrong test. Reruns differ in the last bits
 * from summation order and from normalising a probability — around 1e-13 —
 * so comparing raw floats made every model trip the warning as soon as a
 * rerun happened, offering "0.2028300 vs 0.2028300" as its evidence. A
 * warning that fires on every row is one nobody reads again, which costs
 * more than the warning was ever worth.
 *
 * The default tolerance sits well above float noise and well below
 * anything that could matter: the genuine `team-ratings-v1` determinism
 * delta is 9.65e-06, six orders of magnitude above it.
 */
export function valuesDisagree(
  values: Iterable<number>,
  tolerance = 1e-9,
): boolean {
  const seen = [...values].filter((v) => Number.isFinite(v));
  if (seen.length < 2) return false;
  return Math.max(...seen) - Math.min(...seen) > tolerance;
}

/**
 * A signed figure in bankroll units — a P&L, where the sign IS the point.
 */
export function formatUnits(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}u`;
}

/**
 * A drawdown in bankroll units. Unsigned, because it is a magnitude.
 *
 * `max_drawdown_units` is computed as `max(peak - bankroll)`, so it is
 * always positive and always describes a LOSS. The Forward Test screen
 * rendered it through the signed P&L formatter, which printed a
 * three-unit drawdown as "+3.00u" — directly beside "P&L (paper) +1.20u",
 * where the same plus sign means a gain. Nothing on the screen said which
 * convention applied to which tile, and the reader takes it from the
 * neighbour.
 *
 * A negative input cannot come from that formula. If one arrives it is a
 * defect upstream, and rendering it as a gain would be the worst of the
 * available responses, so it is reported as absent.
 */
export function formatDrawdownUnits(
  n: number | null | undefined,
  digits = 2,
): string {
  if (n === null || n === undefined || !Number.isFinite(n) || n < 0) return '—';
  return `${n.toFixed(digits)}u`;
}

export interface BrierVerdict {
  label: string;
  /** `muted` for indistinguishable, `warning` for a higher score. Never
   *  `success`: a lower Brier on a burned period is not a win. */
  tone: 'muted' | 'warning';
}

/**
 * What the Model Audit table may say about a Brier difference.
 *
 * It said "better than the market", in success green, for any model
 * beating the benchmark by more than the noise band. Those scores are the
 * 2024 and 2025 backtests, and docs/model-governance.md records both as
 * BURNED — "Neither 2024 nor 2025 may be presented as an out-of-sample
 * test result for any model developed or selected after 2026-08-01",
 * because their results were read and compared across six models. A
 * period that cannot support an out-of-sample claim certainly cannot
 * support a superiority claim, and claiming predictive superiority is
 * forbidden outright.
 *
 * The measurement stays. It is a real difference in a real score over a
 * stated set of games, and the wording now says exactly that and stops.
 * The reader can draw their own inference; the screen does not draw it
 * for them and does not colour it as a win.
 *
 * `delta` is model Brier minus benchmark Brier, so NEGATIVE is the lower
 * score. `band` is the noise threshold at this sample size.
 */
export function describeBrierDelta(delta: number, band: number): BrierVerdict {
  if (!Number.isFinite(delta) || !Number.isFinite(band) || Math.abs(delta) < band) {
    return { label: 'indistinguishable from the market', tone: 'muted' };
  }
  return delta < 0
    ? { label: 'lower Brier than the market on these games', tone: 'muted' }
    : { label: 'higher Brier than the market on these games', tone: 'warning' };
}

/**
 * What a slate week is called on screen.
 *
 * Week 0 is the preseason. Nothing rendered it, because the Slate screen
 * built its filter with `if (g.week)` and picked its default with
 * `.filter((g) => g.week && ...)` — both falsy at zero. Eleven preseason
 * fixtures were therefore absent from every week option, and the screen
 * labelled regular-season week 1 "(next)" while those eleven kicked off
 * four weeks sooner. They were also the only games with captured prices,
 * so the Live Slate one click away was showing the games this screen said
 * were not next.
 */
export function weekLabel(week: number | null | undefined): string {
  if (week === null || week === undefined || !Number.isFinite(week)) return '—';
  return week === 0 ? 'Preseason' : `Week ${week}`;
}

interface ScheduledGame {
  week: number | null | undefined;
  kickoff_utc: string;
}

/**
 * The week of the soonest game that has not kicked off, or null.
 *
 * Presence of a week is tested with `!= null`, not for truthiness, which
 * is the whole point — see `weekLabel`. An unparseable kickoff is skipped
 * rather than compared, because `NaN >= now` is false and a game with a
 * broken timestamp should not silently become the answer either way.
 */
export function soonestScheduledWeek(
  games: readonly ScheduledGame[],
  now: number = Date.now(),
): number | null {
  let best: { at: number; week: number } | null = null;
  for (const g of games) {
    if (g.week === null || g.week === undefined) continue;
    const at = new Date(g.kickoff_utc).getTime();
    if (!Number.isFinite(at) || at < now) continue;
    if (best === null || at < best.at) best = { at, week: g.week };
  }
  return best?.week ?? null;
}
