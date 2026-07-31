import { Link } from 'react-router-dom';
import { Card, CardHeader, ErrorState, FreshBadge, LoadingState, Mono, Pill, StaleBanner, Td, Th } from '@fde/ui';
import { useDataset } from '../lib/api';
import { fmtUtc } from '../lib/format';
import { gameLabel } from '../lib/joins';

export default function DataHealth() {
  const { data: ds, isLoading, error } = useDataset();
  if (isLoading) return <LoadingState label="Loading data-health monitors…" />;
  if (error || !ds) return <ErrorState title="Failed to load data health" />;

  const failing = ds.feedStatuses.filter((f) => f.status === 'MISSING' || f.status === 'STALE' || f.status === 'CONFLICTING');

  return (
    <div className="space-y-4">
      {failing.length > 0 ? (
        <StaleBanner>
          {failing.length} feed{failing.length > 1 ? 's' : ''} degraded ({failing.map((f) => f.feed).join(', ')}).
          Critical failures automatically downgrade affected recommendations to DATA INCOMPLETE.
        </StaleBanner>
      ) : null}

      <Card>
        <CardHeader title="Feed monitors" hint="Operational status of every ingestion pipeline and service" />
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <caption className="sr-only">Data feed operational statuses</caption>
            <thead>
              <tr>
                <Th>Feed / service</Th><Th>Status</Th><Th>Last success (UTC)</Th><Th>Last attempt (UTC)</Th>
                <Th>Records</Th><Th>Freshness</Th><Th>Error</Th><Th>Impacted games</Th>
                <Th>Impacted predictions</Th><Th>Resolution</Th>
              </tr>
            </thead>
            <tbody>
              {ds.feedStatuses.map((f) => (
                <tr key={f.feed} className="hover:bg-panel-raised/60">
                  <Td className="font-medium">{f.feed}</Td>
                  <Td><FreshBadge status={f.status} /></Td>
                  <Td><Mono className="text-ink-faint">{f.lastSuccessAt ? fmtUtc(f.lastSuccessAt) : 'never'}</Mono></Td>
                  <Td><Mono className="text-ink-faint">{f.lastAttemptAt ? fmtUtc(f.lastAttemptAt) : '—'}</Mono></Td>
                  <Td><Mono>{f.recordCount.toLocaleString()}</Mono></Td>
                  <Td>
                    <Mono className={f.freshnessMinutes === undefined ? 'text-bad' : f.freshnessMinutes > 12 * 60 ? 'text-warn' : 'text-ink'}>
                      {f.freshnessMinutes === undefined ? '—' : f.freshnessMinutes < 90 ? `${Math.round(f.freshnessMinutes)}m` : `${(f.freshnessMinutes / 60).toFixed(1)}h`}
                    </Mono>
                  </Td>
                  <Td className="max-w-56 whitespace-normal text-[11px] text-bad">{f.error ?? ''}</Td>
                  <Td>
                    {f.impactedGameIds.length === 0 ? (
                      <span className="text-ink-faint">—</span>
                    ) : (
                      f.impactedGameIds.map((g) => (
                        <Link key={g} to={`/game/${g}`} className="mr-1.5 text-accent underline-offset-2 hover:underline">
                          {gameLabel(ds, g)}
                        </Link>
                      ))
                    )}
                  </Td>
                  <Td className="max-w-48 truncate text-[11px] text-ink-faint">
                    {f.impactedPredictionIds.length === 0 ? '—' : f.impactedPredictionIds.join(', ')}
                  </Td>
                  <Td>
                    <Pill tone={f.resolutionStatus === 'OK' ? 'ok' : f.resolutionStatus === 'FAILED' ? 'bad' : 'warn'}>
                      {f.resolutionStatus}
                    </Pill>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card>
        <CardHeader title="Data-quality events" hint="Critical events invalidate affected recommendations automatically" />
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <caption className="sr-only">Data quality event log</caption>
            <thead>
              <tr><Th>At (UTC)</Th><Th>Feed</Th><Th>Severity</Th><Th>Message</Th><Th>Impacted games</Th><Th>Resolved</Th></tr>
            </thead>
            <tbody>
              {ds.dataQualityEvents.map((e) => (
                <tr key={e.id} className="hover:bg-panel-raised/60">
                  <Td><Mono className="text-ink-faint">{fmtUtc(e.createdAt)}</Mono></Td>
                  <Td className="font-medium">{e.feed}</Td>
                  <Td>
                    <Pill tone={e.severity === 'CRITICAL' ? 'bad' : e.severity === 'HIGH' ? 'bad' : e.severity === 'MODERATE' ? 'warn' : 'neutral'}>
                      {e.severity}
                    </Pill>
                  </Td>
                  <Td className="max-w-md whitespace-normal text-ink-muted">{e.message}</Td>
                  <Td>
                    {e.impactedGameIds.map((g) => (
                      <Link key={g} to={`/game/${g}`} className="mr-1.5 text-accent underline-offset-2 hover:underline">
                        {gameLabel(ds, g)}
                      </Link>
                    ))}
                  </Td>
                  <Td>{e.resolvedAt ? <Mono className="text-ink-faint">{fmtUtc(e.resolvedAt)}</Mono> : <Pill tone="warn">open</Pill>}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card>
        <CardHeader title="Audit log" hint="Append-only application audit trail" />
        <div className="max-h-64 overflow-y-auto">
          <table className="w-full border-collapse">
            <caption className="sr-only">Application audit log</caption>
            <thead>
              <tr><Th>At (UTC)</Th><Th>Action</Th><Th>Entity</Th><Th>Entity ID</Th><Th>Detail</Th><Th>User</Th></tr>
            </thead>
            <tbody>
              {[...ds.auditLog].reverse().map((a) => (
                <tr key={a.id}>
                  <Td><Mono className="text-ink-faint">{fmtUtc(a.createdAt)}</Mono></Td>
                  <Td><Mono className="text-ink-muted">{a.action}</Mono></Td>
                  <Td>{a.entity}</Td>
                  <Td><Mono className="text-ink-faint">{a.entityId}</Mono></Td>
                  <Td className="max-w-md whitespace-normal text-[11px] text-ink-muted">{a.detail}</Td>
                  <Td className="text-ink-faint">{a.userId ?? 'system'}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
