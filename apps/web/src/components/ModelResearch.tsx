import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { engineGet } from '../lib/engine';
import { fmtInstant } from '../lib/format';

const status = z.enum(['RESEARCH ONLY', 'INSUFFICIENT EVIDENCE', 'VALIDATION FAILED', 'ELIGIBLE FOR FURTHER REVIEW']);
const metric = z.number().finite().nullable();
const interval = z.tuple([z.number().finite(), z.number().finite()]).refine(([lo, hi]) => lo <= hi);
const responseSchema = z.object({
  status,
  report: z.object({
    run_id: z.string(), created_at: z.string().datetime({ offset: true }), scope: z.string(),
    limitations: z.array(z.string()), recommended_for_review: z.array(z.string()),
    models: z.array(z.object({ name: z.string(), status, n: z.number().int().nonnegative(),
      metrics: z.object({ brier: metric, log_loss: metric, mae_margin: metric, mae_total: metric,
        log_loss_ci90: interval.nullable().optional(), calibration_intercept: metric.optional(),
        calibration_slope: metric.optional(), interval_80_coverage: z.object({margin: metric, total: metric}).optional(),
        closing_benchmark: z.object({n: z.number(), log_loss: metric, brier: metric,
          margin_mae: metric, total_mae: metric, paired_log_loss_delta_ci90: interval.nullable()}).optional(),
      }),
      reasons: z.array(z.string()),
    })),
    estimates: z.array(z.object({ canonical_game_id: z.string(), model: z.string(),
      home_win_probability: z.number().finite().min(0).max(1), margin_mean: z.number().finite(),
      total_mean: z.number().finite(), margin_interval: interval, total_interval: interval,
    })),
  }).nullable(),
});

const number = (value: number | null) => value == null ? 'Unavailable' : value.toFixed(3);

export default function ModelResearch({ gameId }: { gameId?: string }) {
  const query = useQuery({
    queryKey: ['engine', 'model-comparison', gameId ?? 'all'],
    queryFn: async () => responseSchema.parse(await engineGet<unknown>(
      `/v1/research/model-comparison${gameId ? `?canonical_game_id=${encodeURIComponent(gameId)}` : ''}`,
    )),
    staleTime: 60_000, retry: false,
  });
  // A failed refresh must not leave old estimates visible as current evidence.
  const report = query.error ? null : query.data?.report;
  const estimates = gameId ? report?.estimates.filter(item => item.canonical_game_id === gameId) ?? [] : [];
  return <section aria-label="Statistical model research" className="rounded-xl border border-edge bg-panel p-5">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-lg font-semibold text-ink">Model research</h2>
      <button type="button" className="rounded border border-edge px-3 py-2 text-sm text-accent" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh model research</button>
    </div>
    <p className="mt-2 text-sm text-warning">NO BET · Research estimates cannot qualify a betting recommendation.</p>
    {query.isPending ? <p role="status" className="mt-2 text-sm text-ink-muted">Loading model evidence…</p>
      : query.error ? <p role="alert" className="mt-2 text-sm text-ink-muted">Model evidence is unavailable. Retained estimates are hidden until a successful refresh.</p>
        : !report ? <p className="mt-2 text-sm text-ink-muted">INSUFFICIENT EVIDENCE · No completed model comparison is available.</p>
          : <>
            <p className="mt-2 text-sm text-ink">{query.data?.status} · {report.scope}</p>
            <p className="mt-1 text-xs text-ink-muted">Evaluation recorded {fmtInstant(report.created_at)}. Historical performance is not proof of a current betting advantage.</p>
            {gameId && estimates.length === 0 && <p className="mt-3 text-sm text-ink-muted">No stored model estimate for this game. Historical evaluation does not create a current projection.</p>}
            <details className="mt-4 text-sm text-ink-muted"><summary className="cursor-pointer text-accent">Model results and limitations ({report.models.length} approaches)</summary>
              <div className="mt-3 grid gap-3 md:grid-cols-2">{report.models.map(model => <article key={model.name} className="min-w-0 rounded border border-edge p-3">
                <h3 className="break-words font-medium text-ink">{model.name}</h3>
                <p className="mt-1 text-warning">{model.status} · {model.n} games</p>
                <p className="mt-2">Brier {number(model.metrics.brier)} · log loss {number(model.metrics.log_loss)}</p>
                <p>Margin error {number(model.metrics.mae_margin)} points · total error {number(model.metrics.mae_total)} points</p>
                {model.metrics.closing_benchmark && <p className="mt-2">Closing-market benchmark: log loss {number(model.metrics.closing_benchmark.log_loss)} ({model.metrics.closing_benchmark.n} games); margin error {number(model.metrics.closing_benchmark.margin_mae)}, total error {number(model.metrics.closing_benchmark.total_mae)} points. Evaluation reference; prices were not candidate inputs.</p>}
                {model.metrics.log_loss_ci90 && <p className="mt-2">90% week-block interval for log loss: {model.metrics.log_loss_ci90.map(number).join(' to ')}.</p>}
                {model.metrics.interval_80_coverage && <p>Observed coverage of 80% model intervals: margin {number(model.metrics.interval_80_coverage.margin)}, total {number(model.metrics.interval_80_coverage.total)}.</p>}
                {model.metrics.calibration_slope != null && <p>Moneyline calibration slope {number(model.metrics.calibration_slope)}; intercept {number(model.metrics.calibration_intercept ?? null)}. Ideal: 1 and 0.</p>}
                <ul className="mt-2 list-disc space-y-1 pl-4">{model.reasons.map((reason, index) => <li key={`${index}:${reason}`}>{reason}</li>)}</ul>
              </article>)}</div>
              {estimates.length > 0 && <div className="mt-4 space-y-3">{estimates.map(estimate => <p key={`${estimate.canonical_game_id}:${estimate.model}`}>
                <strong className="text-ink">Stored research estimate · {estimate.model}</strong><br />
                Home win {(estimate.home_win_probability * 100).toFixed(1)}% · margin {estimate.margin_mean.toFixed(1)} · total {estimate.total_mean.toFixed(1)}.<br />
                Model intervals: margin {estimate.margin_interval.map(v => v.toFixed(1)).join(' to ')}; total {estimate.total_interval.map(v => v.toFixed(1)).join(' to ')}. These are model distributions, not confidence in a profitable bet.
              </p>)}</div>}
              <ul className="mt-4 list-disc space-y-1 pl-4">{report.limitations.map((reason, index) => <li key={`${index}:${reason}`}>{reason}</li>)}</ul>
              <p className="mt-3 text-xs break-words">Run {report.run_id}. Eligibility for further review is not approval for recommendations. Exact bet365 prices and a validated joint SGP model remain separate requirements.</p>
            </details>
          </>}
  </section>;
}
