import { useMemo } from 'react';
import { MockFdeApi, type EvaluationContext, type FdeApi } from '@fde/api-client';
import { useQuery } from '@tanstack/react-query';
import { useStore } from './store';
import { exposureMaps } from './joins';

/**
 * Single point of data access. Version 1 wires the clearly-labeled mock
 * implementation; the future Python FastAPI service implements the same
 * interface (see docs/api/openapi.yaml) and replaces this singleton without
 * UI changes.
 */
export const api: FdeApi = new MockFdeApi();

export function useDataset() {
  return useQuery({ queryKey: ['dataset'], queryFn: () => api.getDataset(), staleTime: 60_000 });
}

/**
 * Builds the live portfolio-exposure context (real open-bet dollars,
 * converted to fractions of the current bankroll inside the recommendation
 * engine) so per-game/per-team/weekly staking caps are checked against
 * actual state rather than always evaluating as if the portfolio were empty.
 */
function useExposureContext(): EvaluationContext {
  const { data: ds } = useDataset();
  const store = useStore();
  return useMemo(() => {
    if (!ds) return {};
    const { byGame, byTeam } = exposureMaps(ds, store.openBets);
    return {
      bankroll: store.ledger?.bankrollBalance,
      riskControls: store.settings.riskControls,
      existingWeeklyOpenStakePct: store.weeklyExposurePct,
      existingGameExposureByGame: byGame,
      existingTeamExposureByTeam: byTeam,
    };
  }, [ds, store.openBets, store.ledger?.bankrollBalance, store.settings.riskControls, store.weeklyExposurePct]);
}

export function useRecommendations() {
  const context = useExposureContext();
  return useQuery({
    queryKey: ['recommendations', context],
    queryFn: () => api.getAllRecommendations(context),
    staleTime: 60_000,
  });
}

export function useCandidates(gameId: string) {
  return useQuery({
    queryKey: ['candidates', gameId],
    queryFn: () => api.getCandidates(gameId),
    staleTime: 60_000,
  });
}
