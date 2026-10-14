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
    intelligence: z.object({ version: z.string(), cutoff: z.string().datetime({ offset: true }),
      manifest_hash: z.string(), evidence_hash: z.string(), readiness: z.literal('RESEARCH_ONLY_INCOMPLETE'),
      missing: z.array(z.string()), identity_review_count: z.number(),
      sources: z.array(z.object({ dataset: z.string(), source_id: z.string(), source_url: z.string().url(),
        published_at: z.string().nullable(), captured_at: z.string(), status: z.string(),
        coverage: z.object({ source_rows: z.number(), accepted_rows: z.number(), refused_rows: z.number(),
          max_week: z.number().nullable() }),
      })),
      teams: z.array(z.object({ team_id: z.string(), roster_count: z.number(), roster_week: z.number().nullable(),
        recent_games: z.array(z.object({ week: z.number(), canonical_game_id: z.string(),
          metrics: z.record(z.number().finite().nullable()) })),
        players: z.array(z.object({ player_id: z.string(), name: z.string(), position: z.string().nullable(),
          week: z.number(), metrics: z.record(z.number().finite().nullable()), snap_share: z.number().min(0).max(1).nullable(),
          usage: z.object({ status: z.literal('DESCRIPTIVE_ONLY'), sample_games: z.literal(1),
            shares: z.record(z.object({ player_count: z.number().nullable(), team_count: z.number().nullable(),
              share: z.number().min(0).max(1).nullable() })), missing: z.array(z.string()),
          }).optional() })),
        player_count: z.number(), availability_complete: z.literal(false),
        availability: z.array(z.object({ player_id: z.string(), name: z.string(), practice: z.string().nullable(),
          designation: z.string().nullable(), status: z.literal('SECONDARY_UNVERIFIED') })),
        verified_availability: z.array(z.object({ player_id: z.string(), practice: z.string().nullable(),
          designation: z.string().nullable(), source: z.string().nullable(),
          published_at: z.string().nullable(), captured_at: z.string() })),
      })),
    }).optional(),
    quote_count: z.number(), research_records: z.array(z.object({ id: z.number(), status: z.string(), cohort: z.string() })),
    schedule: z.object({ source: z.string(), captured_at: z.string(), capture_age_seconds: z.number() }),
    weather: z.object({ source: z.string(), captured_at: z.string(),
      source_updated_at: z.string().nullable(), temp_f: z.number().nullable(), wind_mph: z.number().nullable(),
    }).nullable(),
  })),
});

export type Intelligence = NonNullable<z.infer<typeof schema>['games'][number]['intelligence']>;

export function useBriefing() {
  return useQuery({ queryKey: ['engine', 'war-room'],
    queryFn: async () => schema.parse(await engineGet<unknown>('/v1/research/briefing')),
    retry: false, refetchInterval: 60_000,
  });
}
