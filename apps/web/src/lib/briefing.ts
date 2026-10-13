import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { engineGet } from './engine';

const schema = z.object({
  version: z.string(), as_of_at: z.string().datetime({ offset: true }), timezone: z.string(),
  today_count: z.number().int().nonnegative(), observed_market_games: z.number().int().nonnegative(),
  verified_bet365_count: z.literal(0), qualified_count: z.literal(0), decision: z.literal('NO_BET'),
  briefing: z.string(), latest_run: z.object({ id: z.string(), status: z.string(),
    finished_at: z.string().nullable(), started_at: z.string().nullable(), error: z.string().nullable(),
  }).nullable(),
  changes: z.array(z.object({ canonical_game_id: z.string(), fields: z.array(z.string()),
    previous_run_id: z.string(), current_run_id: z.string(),
  })),
  games: z.array(z.object({ canonical_game_id: z.string(), away_team_id: z.string(), home_team_id: z.string(),
    kickoff_utc: z.string(), game_status: z.string(), today: z.boolean(), status: z.literal('DATA_INCOMPLETE'),
    missing: z.array(z.string()), markets_observed: z.array(z.string()), books_observed: z.array(z.string()),
    quote_count: z.number(), research_records: z.array(z.object({ id: z.number(), status: z.string(), cohort: z.string() })),
    schedule: z.object({ source: z.string(), captured_at: z.string(), capture_age_seconds: z.number() }),
    weather: z.object({ source: z.string(), captured_at: z.string(),
      source_updated_at: z.string().nullable(), temp_f: z.number().nullable(), wind_mph: z.number().nullable(),
    }).nullable(),
  })),
});

export function useBriefing() {
  return useQuery({ queryKey: ['engine', 'war-room'],
    queryFn: async () => schema.parse(await engineGet<unknown>('/v1/research/briefing')),
    retry: false, refetchInterval: 60_000,
  });
}
