import type { DemoDataset } from '@fde/api-client';
import type { Game, Team } from '@fde/shared-types';

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

export function roofLabel(roof: string): string {
  switch (roof) {
    case 'DOME': return 'Dome';
    case 'OUTDOOR': return 'Outdoor';
    case 'RETRACTABLE_OPEN': return 'Retractable (open)';
    case 'RETRACTABLE_CLOSED': return 'Retractable (closed)';
    default: return roof;
  }
}
