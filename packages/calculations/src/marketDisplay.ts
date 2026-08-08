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
