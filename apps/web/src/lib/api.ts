import { MockFdeApi, type FdeApi } from '@fde/api-client';
import { useQuery } from '@tanstack/react-query';

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

export function useRecommendations() {
  return useQuery({
    queryKey: ['recommendations'],
    queryFn: () => api.getAllRecommendations(),
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
