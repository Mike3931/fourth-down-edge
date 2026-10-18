import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { z } from 'zod';
import { engineGet } from '../lib/engine';
import { fmtInstant } from '../lib/format';

const finite = z.number().finite();
const metric = finite.nullable();
const schema = z.object({ version: z.literal('prospective-scores-v1'), status: z.literal('RESEARCH ONLY'),
  decision: z.literal('NO BET'), count: z.number().int().nonnegative(), evaluated_forecasts: z.number().int().nonnegative(),
  limitations: z.array(z.string()), sample_policy: z.string(),
  checkpoint_statuses:z.array(z.object({horizon:z.string(),state:z.string(),recorded_approaches:z.number().int().nonnegative(),
    window_opens_at:z.string().datetime({offset:true}),reason:z.string().nullable()})).optional().default([]),
  records: z.array(z.object({ id: z.string(), game_id: z.string(), model_version: z.string(), feature_snapshot_id: z.string(),
    horizon: z.enum(['PROS_EARLY','PROS_72H','PROS_24H','PROS_FINAL']), created_at: z.string().datetime({offset:true}),
    current: z.boolean(), state: z.string(), evaluation_state:z.string().optional(), verification_warning:z.string().nullable().optional(), outputs: z.object({ approach:z.string(), home_win_prob:finite.min(0).max(1),
      expected_margin:finite, expected_total:finite, margin_p10:finite, margin_p90:finite,total_p10:finite,total_p90:finite,
      validation_status:z.enum(['RESEARCH ONLY','VALIDATION FAILED']),approval_status:z.literal('research_only'),
      research_only:z.literal(true),missing:z.array(z.string()),training_games:z.number().int().nonnegative(),
    }), evaluation:z.object({home_score:finite,away_score:finite, actual_margin:finite.optional(),actual_total:finite.optional(),
      brier:metric.optional(),margin_signed_error:finite.optional(),total_signed_error:finite.optional(),
      evaluated_at:z.string().optional(),verification:z.string().optional(),winner_correct:z.boolean().nullable().optional()}).nullable(),
    change:z.object({previous_prediction_id:z.string(),home_win_probability_delta:finite,margin_delta:finite,total_delta:finite}).nullable(),
  })),
  performance:z.array(z.object({approach:z.string(),horizon:z.string(),season:z.string(),week:z.string(),
    n:z.number().int().nonnegative(),n_moneyline:z.number().int().nonnegative(),ties:z.number().int().nonnegative(),
    brier:metric,log_loss:metric,margin_mae:metric,total_mae:metric,margin_coverage80:metric,total_coverage80:metric,
    status:z.literal('INSUFFICIENT EVIDENCE'),
  })),
});
const value = (n:number|null) => n == null ? 'Not yet available' : n.toFixed(3);

export default function ProspectiveResearch({gameId}:{gameId?:string}) {
  const query = useQuery({queryKey:['engine','prospective',gameId??'all'],retry:false,staleTime:30_000,refetchInterval:60_000,
    queryFn:async()=>schema.parse(await engineGet<unknown>(`/v1/research/prospective${gameId?`?canonical_game_id=${encodeURIComponent(gameId)}`:''}`)),
  });
  const data = query.error ? undefined : query.data;
  const seen = new Set<string>();
  const latest = data?.records.filter(r=>{const key=`${r.game_id}:${r.outputs.approach}`;if(seen.has(key))return false;seen.add(key);return true;}) ?? [];
  return <section aria-label="Pregame forecast tracking" className="rounded-xl border border-edge bg-panel p-5">
    <div className="flex flex-wrap justify-between gap-3"><h2 className="text-lg font-semibold text-ink">Recorded before kickoff</h2>
      <button type="button" className="rounded border border-edge px-3 py-2 text-sm text-accent" disabled={query.isFetching} onClick={()=>void query.refetch()}>Refresh forecasts</button></div>
    <p className="mt-2 text-sm text-warning">Research only · NO BET. Forecasts are being tested, not recommended wagers.</p>
    {query.error ? <p role="alert" className="mt-3 text-ink-muted">Forecast tracking is unavailable. Cached estimates are hidden.</p>
      : query.isPending ? <p role="status" className="mt-3 text-ink-muted">Loading recorded forecasts…</p>
        : data && <>
          <p className="mt-3 text-sm text-ink-muted">{data.count} forecast records · {data.evaluated_forecasts} evaluated. {data.evaluated_forecasts===0?'No settled prospective sample yet. We must wait for actual games.':'Results remain insufficient to establish reliability.'}</p>
          {data.checkpoint_statuses.length>0 && <ul aria-label="Forecast checkpoints" className="mt-3 space-y-1 text-sm text-ink-muted">
            {data.checkpoint_statuses.map(c=><li key={c.horizon}><strong>{c.horizon.replace('PROS_','')}</strong>: {c.state.replaceAll('_',' ')}
              {c.recorded_approaches>0 ? ` · ${c.recorded_approaches} model approaches` : ''}
              {c.reason ? ` · ${c.reason}` : ''}{c.state==='NOT_YET_DUE' ? ` · opens ${fmtInstant(c.window_opens_at)}` : ''}.</li>)}
          </ul>}
          {latest.length===0 && <p className="mt-3 text-sm text-ink-muted">No pregame forecast has been recorded in this workspace. A research worker must capture eligible evidence before kickoff.</p>}
          <div className="mt-4 grid gap-3 md:grid-cols-2">{latest.slice(0,gameId?6:3).map(r=><article key={r.id} className="min-w-0 rounded border border-edge p-3 text-sm text-ink-muted">
            <Link className="font-semibold text-accent" to={`/live/game/${encodeURIComponent(r.game_id)}`}>{r.game_id}</Link>
            <p className="mt-1 break-words text-ink">{r.outputs.approach}</p>
            <p>{r.current?'Captured pregame estimate':`Historical record · ${r.state}`}</p>
            <p className="mt-2 text-ink">{r.evaluation_state ?? 'Evaluation status unavailable'}</p>
            {r.verification_warning && <p className="text-warning">{r.verification_warning}</p>}
            <p className="mt-2">Home win {(r.outputs.home_win_prob*100).toFixed(1)}% · home margin {r.outputs.expected_margin.toFixed(1)} · total {r.outputs.expected_total.toFixed(1)}</p>
            <p>80% model range: margin {r.outputs.margin_p10.toFixed(1)} to {r.outputs.margin_p90.toFixed(1)}; total {r.outputs.total_p10.toFixed(1)} to {r.outputs.total_p90.toFixed(1)}.</p>
            <p className="mt-2">Recorded {fmtInstant(r.created_at)} · {r.horizon.replace('PROS_','')} checkpoint.</p>
            {r.evaluation && <p>Final score: home {r.evaluation.home_score}, away {r.evaluation.away_score}.</p>}
            {r.evaluation && <p>Actual home margin {value(r.evaluation.actual_margin ?? null)}; actual total {value(r.evaluation.actual_total ?? null)}.
              {' '}Prediction minus outcome: margin {value(r.evaluation.margin_signed_error ?? null)}, total {value(r.evaluation.total_signed_error ?? null)}.
              {' '}Brier {value(r.evaluation.brier ?? null)}. Winner {r.evaluation.winner_correct == null ? 'not scored (tie or no favored team)' : r.evaluation.winner_correct ? 'correct' : 'incorrect'}.
              {' '}Evaluated {r.evaluation.evaluated_at ? fmtInstant(r.evaluation.evaluated_at) : 'timestamp unavailable'}; {r.evaluation.verification}.</p>}
            {r.change && <p className="mt-2">Since prior record: win estimate {(r.change.home_win_probability_delta*100).toFixed(1)} percentage points; margin {r.change.margin_delta.toFixed(1)}; total {r.change.total_delta.toFixed(1)}. Evidence was captured again; unvalidated inputs do not change model estimates.</p>}
            <details className="mt-3"><summary className="cursor-pointer text-accent">Missing inputs and forecast details</summary>
              <p className="mt-2">{r.outputs.validation_status} · {r.outputs.training_games} captured training games. A model win estimate is not confidence in a profitable bet.</p>
              <ul className="mt-2 space-y-1">{r.outputs.missing.map((m,i)=><li key={i}>{m}</li>)}</ul>
              <p className="mt-2 break-all">Model {r.model_version} · frozen evidence {r.feature_snapshot_id} · record {r.id}.</p>
            </details>
          </article>)}</div>
          {!gameId && latest.length>3 && <p className="mt-2 text-xs text-ink-muted">Showing three latest model/game summaries. Open each Game Lab for its full history.</p>}
          <details className="mt-4 text-sm text-ink-muted"><summary className="cursor-pointer text-accent">Prospective performance and all {data.count} forecast records</summary>
            <p className="mt-3">Small samples cannot establish model quality. Moneyline scores exclude and count ties; margin/total errors retain them. Cancelled, postponed and disputed games remain unevaluated.</p>
            <p className="mt-2">{data.sample_policy}</p>
            {data.performance.filter(p=>p.season==='ALL'&&p.week==='ALL').map(p=><p className="mt-3" key={`${p.approach}:${p.horizon}`}>
              {p.approach} · {p.horizon}: {p.n} games, {p.n_moneyline} non-ties, {p.ties} ties. Brier {value(p.brier)} · log loss {value(p.log_loss)} · margin error {value(p.margin_mae)} · total error {value(p.total_mae)}. INSUFFICIENT EVIDENCE.
            </p>)}
            <details className="mt-3"><summary className="cursor-pointer text-accent">Results by season and week</summary>{data.performance.filter(p=>p.season!=='ALL').map(p=><p className="mt-2" key={`${p.approach}:${p.horizon}:${p.season}:${p.week}`}>{p.approach} · {p.horizon} · {p.season}, {p.week==='ALL'?'all weeks':`week ${p.week}`}: {p.n} games; Brier {value(p.brier)}, log loss {value(p.log_loss)}, margin error {value(p.margin_mae)}, total error {value(p.total_mae)}; observed 80% interval coverage: margin {value(p.margin_coverage80)}, total {value(p.total_coverage80)}.</p>)}</details>
            <ul className="mt-3 space-y-2">{data.records.map(r=><li className="break-words" key={r.id}>{r.game_id} · {r.outputs.approach} · {r.horizon} · {fmtInstant(r.created_at)} · {r.evaluation_state ?? r.state} · {r.verification_warning && `${r.verification_warning} · `}{r.evaluation?`final ${r.evaluation.home_score}–${r.evaluation.away_score}`:'Awaiting uncontested final evidence'} · {r.id}</li>)}</ul>
            <ul className="mt-4 list-disc space-y-1 pl-4">{data.limitations.map((l,i)=><li key={i}>{l}</li>)}</ul>
          </details>
        </>}
  </section>;
}
