import { Card, CardHeader, ErrorState, LoadingState, Mono, Pill, Td, Th } from '@fde/ui';
import { useDataset } from '../lib/api';
import { fmtUtc } from '../lib/format';

export default function ModelAudit() {
  const { data: ds, isLoading, error } = useDataset();
  if (isLoading) return <LoadingState label="Loading model registry…" />;
  if (error || !ds) return <ErrorState title="Failed to load model registry" />;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Model registry & audit"
          hint="Every prediction references an exact model version, feature-set version, and artifact hash. Advanced models are placeholders until the external Python analytical service is built and validated."
        />
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <caption className="sr-only">Registered model versions with governance metadata</caption>
            <thead>
              <tr>
                <Th>Model</Th><Th>Version</Th><Th>Status</Th><Th>Target</Th><Th>Algorithm</Th>
                <Th>Training</Th><Th>Validation</Th><Th>Feature set</Th><Th>Calibration</Th>
                <Th>Artifact hash</Th><Th>Git commit</Th><Th>Approved</Th><Th>Drift</Th>
                <Th>Retrained</Th><Th>Next review</Th>
              </tr>
            </thead>
            <tbody>
              {ds.modelVersions.map((m) => (
                <tr key={m.id} className="hover:bg-panel-raised/60 align-top">
                  <Td className="font-medium text-model">{m.name}</Td>
                  <Td><Mono>{m.version}</Mono></Td>
                  <Td>
                    {m.isPlaceholder ? (
                      <Pill tone="warn">PLACEHOLDER</Pill>
                    ) : m.status === 'APPROVED_DEMO' ? (
                      <Pill tone="accent">APPROVED (DEMO)</Pill>
                    ) : (
                      <Pill tone="neutral">{m.status}</Pill>
                    )}
                  </Td>
                  <Td className="max-w-44 whitespace-normal text-ink-muted">{m.target}</Td>
                  <Td className="max-w-52 whitespace-normal text-ink-muted">{m.algorithm}</Td>
                  <Td className="text-ink-faint">{m.trainingPeriod}</Td>
                  <Td className="text-ink-faint">{m.validationPeriod}</Td>
                  <Td><Mono className="text-ink-faint">{m.featureSetVersion}</Mono></Td>
                  <Td><Mono className="text-ink-faint">{m.calibrationVersion}</Mono></Td>
                  <Td><Mono className="text-ink-faint">{m.artifactHash}</Mono></Td>
                  <Td><Mono className="text-ink-faint">{m.gitCommit}</Mono></Td>
                  <Td><Mono className="text-ink-faint">{m.approvedAt ? fmtUtc(m.approvedAt) : '—'}</Mono></Td>
                  <Td>
                    <Mono className={m.driftStatus === 'STABLE' ? 'text-ok' : m.driftStatus === 'UNKNOWN' ? 'text-ink-faint' : 'text-warn'}>
                      {m.driftStatus}
                    </Mono>
                  </Td>
                  <Td><Mono className="text-ink-faint">{m.lastRetrainedAt ? fmtUtc(m.lastRetrainedAt).slice(0, 10) : '—'}</Mono></Td>
                  <Td><Mono className="text-ink-faint">{m.nextReviewAt ? fmtUtc(m.nextReviewAt).slice(0, 10) : '—'}</Mono></Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {ds.modelVersions.map((m) => (
          <Card key={m.id}>
            <CardHeader
              title={<span className="normal-case tracking-normal text-model">{m.name} · {m.version}</span>}
              right={m.isPlaceholder ? <Pill tone="warn">PLACEHOLDER</Pill> : <Pill tone="accent">DEMO</Pill>}
            />
            <div className="space-y-2 p-4 text-xs">
              <p className="text-ink-muted">{m.performanceSummary}</p>
              <div>
                <p className="mb-0.5 font-medium text-ink">Known limitations</p>
                <ul className="list-inside list-disc space-y-0.5 text-ink-muted">
                  {m.knownLimitations.map((l) => <li key={l}>{l}</li>)}
                </ul>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {m.dataDependencies.map((d) => <Pill key={d} tone="neutral">{d}</Pill>)}
              </div>
              <p className="text-ink-faint">
                Horizons: {m.predictionHorizons.join(' · ')}
              </p>
            </div>
          </Card>
        ))}
      </div>

      <p className="text-[11px] text-ink-faint">
        Governance rule: a language model never creates final numerical probabilities. Final predictions come from
        versioned statistical models and deterministic code; the components marked PLACEHOLDER produce illustrative
        demo output only and are excluded from the ensemble (weight 0).
      </p>
    </div>
  );
}
