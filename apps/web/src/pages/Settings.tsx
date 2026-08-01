import { useState } from 'react';
import { Button, Card, CardHeader, Mono, Pill, Term } from '@fde/ui';
import { useStore } from '../lib/store';
import { fmtPct } from '../lib/format';

const TIMEZONES = [
  'UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Phoenix',
  'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Asia/Tokyo', 'Australia/Sydney',
];

export default function Settings() {
  const store = useStore();
  const { settings } = store;
  const [lossBudget, setLossBudget] = useState<string>(settings.monthlyLossBudget?.toString() ?? '');
  const [ack, setAck] = useState(false);
  const [modeError, setModeError] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);

  function enableRealTracking() {
    setModeError(null);
    const budget = Number(lossBudget);
    if (!Number.isFinite(budget) || budget <= 0) {
      setModeError('Set a positive monthly loss budget before enabling real tracking.');
      return;
    }
    if (!ack) {
      setModeError('You must acknowledge the risk statement.');
      return;
    }
    store.updateSettings({
      mode: 'REAL_TRACKING',
      monthlyLossBudget: budget,
      realTrackingAcknowledgedAt: new Date().toISOString(),
    });
  }

  const rc = settings.riskControls;
  const inputCls = 'rounded border border-edge bg-bg px-2 py-1.5 text-xs text-ink focus:border-accent focus:outline-none';

  return (
    <div className="max-w-4xl space-y-4">
      <Card>
        <CardHeader title="Display" />
        <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
          <label className="block text-xs text-ink-muted">
            Odds format
            <select
              value={settings.oddsFormat}
              onChange={(e) => store.updateSettings({ oddsFormat: e.target.value as 'AMERICAN' | 'DECIMAL' })}
              className={`${inputCls} mt-1 w-full`}
            >
              <option value="AMERICAN">American (−110)</option>
              <option value="DECIMAL">Decimal (1.91)</option>
            </select>
          </label>
          <label className="block text-xs text-ink-muted">
            Time zone (display only — storage is always UTC)
            <select
              value={settings.timezone}
              onChange={(e) => store.updateSettings({ timezone: e.target.value })}
              className={`${inputCls} mt-1 w-full`}
            >
              {[settings.timezone, ...TIMEZONES.filter((t) => t !== settings.timezone)].map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Application mode"
          right={<Pill tone={settings.mode === 'PAPER' ? 'accent' : 'warn'}>{settings.mode === 'PAPER' ? 'PAPER MODE' : 'REAL TRACKING MODE'}</Pill>}
        />
        <div className="space-y-3 p-4 text-xs">
          <p className="text-ink-muted">
            Paper betting is the default. Real Tracking Mode only records wagers you have already placed manually
            elsewhere — this software never places, submits, or automates wagers, and never connects to a sportsbook.
          </p>
          {settings.mode === 'PAPER' ? (
            <div className="space-y-2 rounded border border-edge bg-panel-raised/50 p-3">
              <p className="font-medium text-ink">Enable Real Tracking Mode</p>
              <label className="block text-ink-muted">
                Required monthly loss budget (USD)
                <input
                  type="number" min={0} step={10} value={lossBudget}
                  onChange={(e) => setLossBudget(e.target.value)}
                  className={`${inputCls} mt-1 w-40 font-mono`}
                  aria-describedby="loss-budget-hint"
                />
              </label>
              <p id="loss-budget-hint" className="text-[11px] text-ink-faint">
                A hard monthly cap on acceptable losses. Reaching it means stopping entirely for the month.
              </p>
              <label className="flex items-start gap-2 text-ink">
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5 accent-[#d9a13b]" />
                I understand that no model guarantees profit, that demo thresholds are not validated, and that I alone
                am responsible for any real wager I record here.
              </label>
              {modeError ? <p role="alert" className="text-bad">{modeError}</p> : null}
              <Button variant="default" onClick={enableRealTracking}>Enable real tracking</Button>
            </div>
          ) : (
            <Button onClick={() => store.updateSettings({ mode: 'PAPER' })}>Return to paper mode</Button>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="Bankroll risk controls" hint="Conservative defaults; staking can only get more conservative than Kelly" />
        <div className="overflow-x-auto p-4">
          <table className="w-full max-w-xl border-collapse text-xs">
            <tbody>
              {[
                [<Term key="k" term="Kelly fraction" def="Fraction of the full Kelly stake used. 0.25 = quarter Kelly." />, fmtPct(rc.kellyFraction, 0)],
                ['Max per wager', fmtPct(rc.maxPerBetPctOfBankroll, 2)],
                ['Max per game', fmtPct(rc.maxPerGamePct, 2)],
                ['Max weekly per team', fmtPct(rc.maxPerTeamWeeklyPct, 2)],
                ['Max correlated cluster', fmtPct(rc.maxCorrelatedClusterPct, 2)],
                ['Max weekly open stake', fmtPct(rc.maxWeeklyOpenStakePct, 2)],
                ['Min edge for BET (demo threshold)', fmtPct(rc.minEdgeForBet, 1)],
                ['Min data completeness', fmtPct(rc.minDataCompletenessScore, 0)],
                ['Max manual-price age', `${rc.maxPriceAgeMinutes} min`],
              ].map(([k, v], i) => (
                <tr key={i} className="border-b border-edge/60">
                  <td className="py-1.5 pr-4 text-ink-muted">{k}</td>
                  <td className="py-1.5"><Mono className="text-ink">{v}</Mono></td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="mt-3 list-inside list-disc space-y-0.5 text-[11px] text-ink-faint">
            <li>No martingale. No loss chasing. Stakes never increase because of a losing streak.</li>
            <li>No parlay recommendations. No automatic wagering. Ever.</li>
            <li>Demo thresholds are labeled as such and are not historically validated.</li>
          </ul>
        </div>
      </Card>

      <Card>
        <CardHeader title="Data export & deletion" />
        <div className="flex flex-wrap items-center gap-3 p-4">
          <Button onClick={store.exportData}>Export my data (JSON)</Button>
          {confirmReset ? (
            <span className="flex items-center gap-2 text-xs">
              <span className="text-bad">Delete all local data (ledger, settings, manual prices)?</span>
              <Button variant="danger" onClick={() => { store.resetData(); setConfirmReset(false); }}>Yes, delete</Button>
              <Button variant="ghost" onClick={() => setConfirmReset(false)}>Cancel</Button>
            </span>
          ) : (
            <Button variant="danger" onClick={() => setConfirmReset(true)}>Delete local data…</Button>
          )}
          <p className="w-full text-[11px] text-ink-faint">
            This application stores no sportsbook credentials, cookies, or sessions, and no payment-card data —
            there is nothing of that kind to export or delete.
          </p>
        </div>
      </Card>
    </div>
  );
}
