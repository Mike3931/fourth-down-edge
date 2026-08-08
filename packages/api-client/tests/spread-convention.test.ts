import { describe, expect, it } from 'vitest';
import { generateDemoDataset } from '../src/dataset';
import { evaluateCandidates } from '../src/evaluate';

/**
 * Which team does a displayed spread belong to?
 *
 * There are TWO conventions in this repository and they are opposites:
 *
 *   `OddsSnapshot.line` is HOME-RELATIVE. So is every quote the Python
 *   engine stores — `_write_quote` negates the away point on capture, so
 *   both rows of a book carry the home team's number.
 *
 *   `Recommendation.line` is SELECTION-RELATIVE. The candidate builder
 *   negates the home line when it pushes the AWAY side, so the value is
 *   already the number the selected side is getting.
 *
 * Both are fine. Mixing them is not, and the mixture is invisible: an
 * inverted spread is a perfectly plausible spread. The live screen was
 * rendering a home-relative line beside the label "AWAY" — a book hanging
 * Carolina at -1.5 appeared as "AWAY +1.5" — and nothing caught it because
 * every value on the page looked like a real line.
 *
 * These pin the demo side of that boundary. The engine side is pinned by
 * apps/api/tests/test_storage_conventions.py, which drives the real
 * capture path, and the display side by
 * packages/calculations/tests/marketDisplay.test.ts.
 *
 * If someone reads the negation in `push('SPREAD', 'AWAY', -spread.line)`
 * as a bug and removes it, every away-side pick in the app silently starts
 * naming a team beside its opponent's number. These fail first.
 */

describe('the demo recommendation line is selection-relative', () => {
  const ds = generateDemoDataset();

  // Evaluate every game rather than a chosen one: a sign convention that
  // holds for a single hand-picked fixture proves nothing, and picking the
  // fixture is how the original error was reached.
  //
  // `evaluateGame` returns only the winning candidate, so the sides are
  // compared at `evaluateCandidates`, which is where both exist.
  const games = ds.games.map((g) => ({
    id: g.id,
    homeTeamId: g.homeTeamId,
    awayTeamId: g.awayTeamId,
    candidates: evaluateCandidates(ds, g.id, ds.demoNow),
  }));

  it('evaluates a non-trivial number of games', () => {
    // Guards the sweep below from passing vacuously.
    expect(games.length).toBeGreaterThan(5);
  });

  it('gives the away side the exact negation of the home side', () => {
    let compared = 0;
    for (const g of games) {
      const cands = g.candidates;
      const home = cands.find((c) => c.market === 'SPREAD' && c.selection === 'HOME');
      const away = cands.find((c) => c.market === 'SPREAD' && c.selection === 'AWAY');
      if (!home || !away || home.line === undefined || away.line === undefined) continue;
      compared += 1;
      expect(home.line + away.line, `${g.id}: home ${home.line} / away ${away.line}`).toBe(0);
    }
    expect(compared, 'no spread pairs were compared — the sweep proved nothing').toBeGreaterThan(5);
  });

  it('never emits negative zero on a pick-em, which renders as "-0"', () => {
    for (const g of games) {
      for (const c of g.candidates) {
        if (c.line === 0) expect(Object.is(c.line, -0)).toBe(false);
      }
    }
  });

  it('leaves totals unmirrored, since OVER and UNDER share one number', () => {
    let compared = 0;
    for (const g of games) {
      const cands = g.candidates;
      const over = cands.find((c) => c.market === 'TOTAL' && c.selection === 'OVER');
      const under = cands.find((c) => c.market === 'TOTAL' && c.selection === 'UNDER');
      if (!over || !under || over.line === undefined || under.line === undefined) continue;
      compared += 1;
      expect(over.line).toBe(under.line);
    }
    expect(compared).toBeGreaterThan(5);
  });

  it('agrees with the odds snapshot on the HOME side specifically', () => {
    // The two conventions must coincide exactly where they overlap. If
    // they disagree here, one of the two layers has drifted.
    let compared = 0;
    for (const g of games) {
      // Same cutoff the candidate builder used, so this compares like
      // with like rather than against a snapshot it could not have seen.
      const snap = ds.oddsSnapshots
        .filter(
          (o) =>
            o.gameId === g.id &&
            o.market === 'SPREAD' &&
            o.line !== undefined &&
            o.observedAt <= ds.demoNow,
        )
        .sort((a, b) => a.observedAt.localeCompare(b.observedAt))
        .at(-1);
      const home = g.candidates.find(
        (c) => c.market === 'SPREAD' && c.selection === 'HOME',
      );
      if (!snap || !home || home.line === undefined) continue;
      compared += 1;
      expect(home.line, `${g.id}`).toBe(snap.line);
    }
    expect(compared).toBeGreaterThan(5);
  });
});
