import type { DemoDataset } from '@fde/api-client';
import type { Bet, Game, Team } from '@fde/shared-types';

/** Small join helpers over the dataset. */

export function teamById(ds: DemoDataset, id: string): Team {
  return ds.teams.find((t) => t.id === id) ?? { id, name: id, abbreviation: '???', conference: 'AFC', division: 'EAST' };
}

export function gameById(ds: DemoDataset, id: string): Game | undefined {
  return ds.games.find((g) => g.id === id) ?? ds.priorGames.find((g) => g.id === id);
}

export function gameLabel(ds: DemoDataset, gameId: string): string {
  const g = gameById(ds, gameId);
  if (!g) return gameId;
  return `${teamById(ds, g.awayTeamId).abbreviation} @ ${teamById(ds, g.homeTeamId).abbreviation}`;
}

export function gameLabelLong(ds: DemoDataset, gameId: string): string {
  const g = gameById(ds, gameId);
  if (!g) return gameId;
  return `${teamById(ds, g.awayTeamId).name} at ${teamById(ds, g.homeTeamId).name}`;
}

export function stadiumById(ds: DemoDataset, id: string) {
  return ds.stadiums.find((s) => s.id === id);
}

export function playerById(ds: DemoDataset, id: string) {
  return ds.players.find((p) => p.id === id);
}

/**
 * Existing open-stake exposure by game and by team, in dollars. Shared by
 * the recommendation engine's staking-cap inputs and the Bet Portfolio
 * display so both read from a single definition of "current exposure" and
 * can never silently drift apart.
 */
export function exposureMaps(ds: DemoDataset, openBets: Bet[]): { byGame: Record<string, number>; byTeam: Record<string, number> } {
  const byGame: Record<string, number> = {};
  const byTeam: Record<string, number> = {};
  for (const b of openBets) {
    byGame[b.gameId] = (byGame[b.gameId] ?? 0) + b.stake;
    const g = gameById(ds, b.gameId);
    if (!g) continue;
    const teamId = b.selection === 'HOME' ? g.homeTeamId : b.selection === 'AWAY' ? g.awayTeamId : undefined;
    if (teamId) byTeam[teamId] = (byTeam[teamId] ?? 0) + b.stake;
  }
  return { byGame, byTeam };
}

/**
 * "<name> <version>" for a model version id, e.g. "fde-ensemble 0.3.0-demo".
 * Always derived from model_versions rather than hardcoded — the display
 * string must never be able to drift out of sync with the actual model
 * registry if the official model ever changes or is retrained.
 */
export function modelVersionLabel(ds: DemoDataset, modelVersionId: string | undefined): string {
  if (!modelVersionId) return '—';
  const m = ds.modelVersions.find((v) => v.id === modelVersionId);
  return m ? `${m.name} ${m.version}` : modelVersionId;
}

export function roofLabel(roof: string): string {
  switch (roof) {
    case 'DOME': return 'Dome';
    case 'OUTDOOR': return 'Outdoor';
    case 'RETRACTABLE_OPEN': return 'Retractable (open)';
    case 'RETRACTABLE_CLOSED': return 'Retractable (closed)';
    default: return roof;
  }
}
