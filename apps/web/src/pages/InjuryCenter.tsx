import { Link } from 'react-router-dom';
import { Card, CardHeader, EmptyState, ErrorState, LoadingState, Mono, Pill, Td, Th } from '@fde/ui';
import { useDataset } from '../lib/api';
import { fmtPct, fmtUtc } from '../lib/format';
import { gameLabel, playerById, teamById } from '../lib/joins';

export default function InjuryCenter() {
  const { data: ds, isLoading, error } = useDataset();
  if (isLoading) return <LoadingState label="Loading injury intelligence…" />;
  if (error || !ds) return <ErrorState title="Failed to load injuries" />;

  const rows = ds.injuryReports
    .map((r) => {
      const avail = ds.availabilitySnapshots.find((a) => a.gameId === r.gameId && a.playerId === r.playerId);
      const player = playerById(ds, r.playerId);
      return { r, avail, player };
    })
    .sort((a, b) => (a.avail?.activeProbability ?? 1) - (b.avail?.activeProbability ?? 1));

  const highlights: Array<{ label: string; detail: string }> = [];
  for (const { r, avail, player } of rows) {
    if (!player || !avail) continue;
    if (player.position === 'QB' && avail.activeProbability > 0.15 && avail.activeProbability < 0.85) {
      highlights.push({ label: 'Quarterback uncertainty', detail: `${player.name} (${gameLabel(ds, r.gameId)}) — active probability ${fmtPct(avail.activeProbability, 0)}` });
    }
    if (['LT', 'LG', 'C', 'RG', 'RT'].includes(player.position) && avail.activeProbability < 0.9) {
      highlights.push({ label: 'Offensive-line continuity', detail: `${player.name} (${player.position}, ${gameLabel(ds, r.gameId)}) — replacement quality ${fmtPct(avail.replacementQuality, 0)}` });
    }
    if (r.conflictWarning) {
      highlights.push({ label: 'Conflicting reports', detail: `${player.name} (${gameLabel(ds, r.gameId)}) — ${r.conflictWarning}` });
    }
    if (r.practiceWed === 'FULL' && r.practiceThu === 'DNP') {
      highlights.push({ label: 'Practice-status deterioration', detail: `${player.name} (${gameLabel(ds, r.gameId)}) went FULL → DNP` });
    }
  }
  // Position-group clusters
  const byTeamPos = new Map<string, number>();
  for (const { r, player } of rows) {
    if (!player) continue;
    const key = `${player.teamId}:${['LT', 'LG', 'C', 'RG', 'RT'].includes(player.position) ? 'OL' : player.position}:${r.gameId}`;
    byTeamPos.set(key, (byTeamPos.get(key) ?? 0) + 1);
  }
  for (const [key, n] of byTeamPos) {
    if (n >= 2) {
      const [teamId, pos, gameId] = key.split(':') as [string, string, string];
      highlights.push({ label: 'Multiple injuries in one position group', detail: `${teamById(ds, teamId).name}: ${n} ${pos} injuries (${gameLabel(ds, gameId)})` });
    }
  }

  const practiceCell = (v: string) => (
    <Mono
      className={
        v === 'DNP' ? 'text-bad' : v === 'LIMITED' ? 'text-warn' : v === 'FULL' ? 'text-ok' : 'text-ink-faint'
      }
    >
      {v === 'NO_DATA' ? '—' : v}
    </Mono>
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Injury highlights" hint="Player availability is probabilistic: inactive / active-restricted / active-ordinary" />
        <div className="grid gap-2 p-3 md:grid-cols-2 xl:grid-cols-3">
          {highlights.length === 0 ? (
            <EmptyState title="No high-priority injury situations" />
          ) : (
            highlights.map((h, i) => (
              <div key={i} className="rounded border border-warn/40 bg-warn/5 px-3 py-2">
                <p className="text-xs font-semibold text-warn">{h.label}</p>
                <p className="mt-0.5 text-[11px] text-ink-muted">{h.detail}</p>
              </div>
            ))
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title={`Injury reports — ${rows.length} listed players (fictional demo names)`} />
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <caption className="sr-only">Injury reports with practice progression and availability probabilities</caption>
            <thead>
              <tr>
                <Th>Player</Th><Th>Team</Th><Th>Pos</Th><Th>Game</Th><Th>Injury</Th>
                <Th>Wed</Th><Th>Thu</Th><Th>Fri</Th><Th>Designation</Th>
                <Th>P(active)</Th><Th>Snap share</Th><Th>P(restricted)</Th>
                <Th>Replacement</Th><Th>Repl quality</Th><Th>Team impact</Th><Th>Conf</Th>
                <Th>Source</Th><Th>Source @ (UTC)</Th><Th>Observed @ (UTC)</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ r, avail, player }) => (
                <tr key={r.id} className="hover:bg-panel-raised/60">
                  <Td className="font-medium">{player?.name ?? r.playerId}</Td>
                  <Td>{player ? teamById(ds, player.teamId).abbreviation : '—'}</Td>
                  <Td>{player?.position}</Td>
                  <Td>
                    <Link to={`/game/${r.gameId}`} className="text-accent underline-offset-2 hover:underline">
                      {gameLabel(ds, r.gameId)}
                    </Link>
                  </Td>
                  <Td className="text-ink-muted">{r.injury}</Td>
                  <Td>{practiceCell(r.practiceWed)}</Td>
                  <Td>{practiceCell(r.practiceThu)}</Td>
                  <Td>{practiceCell(r.practiceFri)}</Td>
                  <Td>
                    <Mono className={r.designation === 'OUT' || r.designation === 'IR' ? 'text-bad' : r.designation === 'DOUBTFUL' ? 'text-warn' : r.designation === 'QUESTIONABLE' ? 'text-warn' : 'text-ink-faint'}>
                      {r.designation}
                    </Mono>
                  </Td>
                  <Td><Mono>{avail ? fmtPct(avail.activeProbability, 0) : '—'}</Mono></Td>
                  <Td><Mono>{avail ? fmtPct(avail.expectedSnapShareIfActive, 0) : '—'}</Mono></Td>
                  <Td><Mono>{avail ? fmtPct(avail.restrictionProbability, 0) : '—'}</Mono></Td>
                  <Td className="text-ink-muted">{avail?.replacementPlayerId ? playerById(ds, avail.replacementPlayerId)?.name : '—'}</Td>
                  <Td><Mono>{avail ? fmtPct(avail.replacementQuality, 0) : '—'}</Mono></Td>
                  <Td><Mono>{avail ? `${avail.estimatedTeamImpactPts.toFixed(1)} pts` : '—'}</Mono></Td>
                  <Td><Mono>{avail ? fmtPct(avail.confidence, 0) : '—'}</Mono></Td>
                  <Td className="text-ink-faint">{r.source}</Td>
                  <Td><Mono className="text-ink-faint">{fmtUtc(r.sourceUpdatedAt)}</Mono></Td>
                  <Td><Mono className="text-ink-faint">{fmtUtc(r.observedAt)}</Mono></Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex flex-wrap items-center gap-2 px-3 py-2">
          {ds.injuryReports.some((r) => r.conflictWarning) && (
            <Pill tone="bad">≠ conflict warnings present — see highlighted rows</Pill>
          )}
          <p className="text-[11px] text-ink-faint">
            Three player states are modeled: INACTIVE, ACTIVE-RESTRICTED, ACTIVE-ORDINARY. All player names are fictional.
          </p>
        </div>
      </Card>
    </div>
  );
}
