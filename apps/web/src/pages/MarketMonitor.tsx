import { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useQueryClient } from '@tanstack/react-query';
import { Button, Card, CardHeader, ErrorState, LoadingState, Mono, Pill, StaleBanner, Td, Th } from '@fde/ui';
import { ageMinutes } from '@fde/calculations';
import { latestSnapshotForDisplay } from '@fde/api-client';
import type { ManualBookPrice, MarketType, SelectionSide } from '@fde/shared-types';
import { api, useDataset, useRecommendations } from '../lib/api';
import { useStore } from '../lib/store';
import { fmtAgo, fmtLine, fmtMarketLine, fmtOdds, fmtUtc } from '../lib/format';
import { gameLabel } from '../lib/joins';

const priceSchema = z.object({
  gameId: z.string().min(1, 'Select a game'),
  sportsbook: z.literal('bet365 (manual entry)'),
  market: z.enum(['MONEYLINE', 'SPREAD', 'TOTAL']),
  selection: z.enum(['HOME', 'AWAY', 'OVER', 'UNDER']),
  line: z.union([z.coerce.number().multipleOf(0.5, 'Lines are quoted in halves'), z.literal('')]).optional(),
  american: z.coerce
    .number()
    .int('American odds are whole numbers')
    .refine((v) => Math.abs(v) >= 100, 'American odds magnitude must be at least 100'),
  observedMinutesAgo: z.coerce.number().min(0, 'Cannot be negative').max(240, 'Too old — re-check the price'),
  confirmedVisible: z.literal(true, { errorMap: () => ({ message: 'You must confirm the price is currently visible' }) }),
});

type PriceForm = z.infer<typeof priceSchema>;

export default function MarketMonitor() {
  const { data: ds, isLoading, error } = useDataset();
  const { data: recs } = useRecommendations();
  const store = useStore();
  const queryClient = useQueryClient();
  const [submitOk, setSubmitOk] = useState<string | null>(null);
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  const form = useForm<PriceForm>({
    resolver: zodResolver(priceSchema),
    defaultValues: {
      sportsbook: 'bet365 (manual entry)',
      market: 'SPREAD',
      selection: 'HOME',
      observedMinutesAgo: 0,
      confirmedVisible: undefined as unknown as true,
    },
  });

  // The API is the source of truth for price records; the local store mirrors
  // them for export. Dedupe by id so a record never appears twice.
  const allManualPrices = useMemo(() => {
    const byId = new Map<string, ManualBookPrice>();
    for (const p of [...(ds?.manualPrices ?? []), ...store.localManualPrices]) byId.set(p.id, p);
    return [...byId.values()].sort((a, b) => b.enteredAt.localeCompare(a.enteredAt));
  }, [ds, store.localManualPrices]);

  if (isLoading) return <LoadingState label="Loading market data…" />;
  if (error || !ds) return <ErrorState title="Failed to load market data" />;

  async function onSubmit(values: PriceForm) {
    setSubmitOk(null);
    setSubmitErr(null);
    const observedAt = new Date(new Date(ds!.demoNow).getTime() - values.observedMinutesAgo * 60_000)
      .toISOString().replace('.000Z', 'Z');
    try {
      const rec = await api.submitManualPrice({
        userId: store.settings.userId,
        gameId: values.gameId,
        sportsbook: values.sportsbook,
        market: values.market as MarketType,
        selection: values.selection as SelectionSide,
        line: values.line === '' || values.line === undefined ? undefined : Number(values.line),
        american: values.american,
        priceObservedAt: observedAt,
        enteredAt: ds!.demoNow,
        confirmedVisible: values.confirmedVisible,
      });
      store.addManualPrice(rec);
      await queryClient.invalidateQueries({ queryKey: ['recommendations'] });
      await queryClient.invalidateQueries({ queryKey: ['dataset'] });
      setSubmitOk(`Recorded immutable price ${rec.id}: ${gameLabel(ds!, rec.gameId)} ${rec.market} ${rec.selection} @ ${rec.american}`);
      form.reset({ ...form.getValues(), american: undefined as unknown as number, confirmedVisible: undefined as unknown as true });
    } catch (e) {
      setSubmitErr(e instanceof Error ? e.message : String(e));
    }
  }

  const staleOdds = ds.feedStatuses.find((f) => f.feed === 'odds' && (f.status === 'STALE' || f.status === 'MISSING'));

  const err = (name: keyof PriceForm) =>
    form.formState.errors[name] ? (
      <p role="alert" className="mt-0.5 text-[11px] text-bad">{form.formState.errors[name]?.message as string}</p>
    ) : null;

  const inputCls = 'w-full rounded border border-edge bg-bg px-2 py-1.5 text-xs text-ink focus:border-accent focus:outline-none';

  return (
    <div className="space-y-4">
      {staleOdds ? (
        <StaleBanner>
          Odds feed {staleOdds.status}: {staleOdds.impactedGameIds.map((g) => gameLabel(ds, g)).join(', ')} —
          affected recommendations are downgraded until data recovers.
        </StaleBanner>
      ) : null}

      {/* Odds board */}
      <Card>
        <CardHeader title="Odds board — mock consensus" hint="Opening vs current across all three markets; movement is current − open" />
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <caption className="sr-only">Opening and current consensus odds by game</caption>
            <thead>
              <tr>
                <Th>Game</Th>
                <Th>Open spread</Th><Th>Curr spread</Th><Th>Move</Th>
                <Th>Open total</Th><Th>Curr total</Th><Th>Move</Th>
                <Th>Curr ML (A/H)</Th>
                <Th>Snapshot @ (UTC)</Th><Th>Age</Th>
                <Th>Target BET price</Th><Th>Watch band</Th>
              </tr>
            </thead>
            <tbody>
              {ds.games.map((g) => {
                const open = ds.oddsSnapshots.find((o) => o.gameId === g.id && o.market === 'SPREAD' && o.isOpening);
                const cur = latestSnapshotForDisplay(ds.oddsSnapshots, g.id, 'SPREAD');
                const openT = ds.oddsSnapshots.find((o) => o.gameId === g.id && o.market === 'TOTAL' && o.isOpening);
                const curT = latestSnapshotForDisplay(ds.oddsSnapshots, g.id, 'TOTAL');
                const curMl = latestSnapshotForDisplay(ds.oddsSnapshots, g.id, 'MONEYLINE');
                const rec = recs?.find((r) => r.gameId === g.id);
                const age = cur ? ageMinutes(cur.observedAt, ds.demoNow) : Infinity;
                const spreadMove = cur?.line !== undefined && open?.line !== undefined ? cur.line - open.line : 0;
                const totalMove = curT?.line !== undefined && openT?.line !== undefined ? curT.line - openT.line : 0;
                return (
                  <tr key={g.id} className="hover:bg-panel-raised/60">
                    <Td className="font-medium">{gameLabel(ds, g.id)}</Td>
                    <Td><Mono>{fmtLine(open?.line)}</Mono></Td>
                    <Td><Mono>{fmtLine(cur?.line)}</Mono></Td>
                    <Td><Mono className={Math.abs(spreadMove) >= 1 ? 'text-warn' : 'text-ink-faint'}>{spreadMove === 0 ? '—' : fmtLine(Math.round(spreadMove * 10) / 10)}</Mono></Td>
                    <Td><Mono>{openT?.line ?? '—'}</Mono></Td>
                    <Td><Mono>{curT?.line ?? '—'}</Mono></Td>
                    <Td><Mono className={Math.abs(totalMove) >= 1 ? 'text-warn' : 'text-ink-faint'}>{totalMove === 0 ? '—' : fmtLine(Math.round(totalMove * 10) / 10)}</Mono></Td>
                    <Td>
                      <Mono>
                        {curMl ? `${fmtOdds(curMl.awayAmerican, store.settings.oddsFormat)} / ${fmtOdds(curMl.homeAmerican, store.settings.oddsFormat)}` : '—'}
                      </Mono>
                    </Td>
                    <Td><Mono className="text-ink-faint">{cur ? fmtUtc(cur.observedAt) : '—'}</Mono></Td>
                    <Td>
                      <Mono className={age > 120 ? 'text-bad' : age > 30 ? 'text-warn' : 'text-ok'}>
                        {Number.isFinite(age) ? `${Math.round(age)}m` : '—'}
                      </Mono>
                    </Td>
                    <Td><Mono>{rec?.targetPrice !== undefined ? fmtOdds(rec.targetPrice, store.settings.oddsFormat) : '—'}</Mono></Td>
                    <Td className="text-[11px] text-ink-muted">
                      {rec?.status === 'WATCH' ? `qualify ≥ ${fmtOdds(rec.targetPrice ?? -110, store.settings.oddsFormat)}; pass ≤ ${fmtOdds(rec.invalidationPrice ?? -125, store.settings.oddsFormat)}` : '—'}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="px-3 py-2 text-[11px] text-ink-faint">
          Median/best across books and closing-line capture activate when the real odds provider is connected; v1 shows a
          single mocked consensus book. No sportsbook is scraped or automated.
        </p>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Manual price entry */}
        <Card>
          <CardHeader
            title="Manual bet365 price entry"
            hint="Prices are entered by you, never scraped. Each entry creates a new immutable record."
          />
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-3 p-4" noValidate>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="mp-game" className="mb-0.5 block text-[11px] font-medium text-ink-muted">Game</label>
                <select id="mp-game" {...form.register('gameId')} className={inputCls}>
                  <option value="">Select…</option>
                  {ds.games.map((g) => (
                    <option key={g.id} value={g.id}>{gameLabel(ds, g.id)}</option>
                  ))}
                </select>
                {err('gameId')}
              </div>
              <div>
                <label htmlFor="mp-book" className="mb-0.5 block text-[11px] font-medium text-ink-muted">Sportsbook</label>
                <input id="mp-book" value="bet365 (manual entry)" readOnly aria-readonly className={`${inputCls} text-ink-muted`} {...form.register('sportsbook')} />
              </div>
              <div>
                <label htmlFor="mp-market" className="mb-0.5 block text-[11px] font-medium text-ink-muted">Market</label>
                <select id="mp-market" {...form.register('market')} className={inputCls}>
                  <option value="SPREAD">SPREAD</option>
                  <option value="TOTAL">TOTAL</option>
                  <option value="MONEYLINE">MONEYLINE</option>
                </select>
              </div>
              <div>
                <label htmlFor="mp-selection" className="mb-0.5 block text-[11px] font-medium text-ink-muted">Selection</label>
                <select id="mp-selection" {...form.register('selection')} className={inputCls}>
                  <option value="HOME">HOME</option>
                  <option value="AWAY">AWAY</option>
                  <option value="OVER">OVER</option>
                  <option value="UNDER">UNDER</option>
                </select>
              </div>
              <div>
                <label htmlFor="mp-line" className="mb-0.5 block text-[11px] font-medium text-ink-muted">Line (blank for moneyline)</label>
                <input id="mp-line" type="number" step="0.5" placeholder="-3.5" {...form.register('line')} className={`${inputCls} font-mono`} />
                {err('line')}
              </div>
              <div>
                <label htmlFor="mp-american" className="mb-0.5 block text-[11px] font-medium text-ink-muted">American odds</label>
                <input id="mp-american" type="number" step="1" placeholder="-105" {...form.register('american')} className={`${inputCls} font-mono`} />
                {err('american')}
              </div>
              <div>
                <label htmlFor="mp-observed" className="mb-0.5 block text-[11px] font-medium text-ink-muted">Observed how many minutes ago?</label>
                <input id="mp-observed" type="number" min={0} {...form.register('observedMinutesAgo')} className={`${inputCls} font-mono`} />
                {err('observedMinutesAgo')}
              </div>
            </div>
            <label className="flex items-start gap-2 text-xs text-ink">
              <input type="checkbox" {...form.register('confirmedVisible')} className="mt-0.5 accent-[#37b8c8]" />
              I confirm this price is currently visible and was entered manually.
            </label>
            {err('confirmedVisible')}
            <Button type="submit" variant="primary" disabled={form.formState.isSubmitting}>
              Record immutable price
            </Button>
            {submitOk ? <p className="text-xs text-ok">{submitOk}</p> : null}
            {submitErr ? <p role="alert" className="text-xs text-bad">{submitErr}</p> : null}
            <p className="text-[11px] leading-relaxed text-ink-faint">
              This application never scrapes bet365, never automates any sportsbook, never connects to undocumented
              endpoints, and never stores sportsbook credentials or sessions. Entered prices are append-only: a new
              observation creates a new record and prior records are preserved.
            </p>
          </form>
        </Card>

        {/* Manual price history */}
        <Card>
          <CardHeader title="Manual price records (immutable)" hint="Newest first; confirmation age drives BET eligibility" />
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <caption className="sr-only">Immutable manually entered sportsbook prices</caption>
              <thead>
                <tr>
                  <Th>ID</Th><Th>Game</Th><Th>Market</Th><Th>Sel</Th><Th>Line</Th><Th>Price</Th>
                  <Th>Observed @ (UTC)</Th><Th>Entered @ (UTC)</Th><Th>Age</Th><Th>Confirmed</Th>
                </tr>
              </thead>
              <tbody>
                {allManualPrices.map((m) => {
                  const age = ageMinutes(m.enteredAt, ds.demoNow);
                  return (
                    <tr key={m.id} className="hover:bg-panel-raised/60">
                      <Td><Mono className="text-ink-faint">{m.id}</Mono></Td>
                      <Td>{gameLabel(ds, m.gameId)}</Td>
                      <Td>{m.market}</Td>
                      <Td>{m.selection}</Td>
                      <Td><Mono>{fmtMarketLine(m.market, m.line)}</Mono></Td>
                      <Td><Mono>{fmtOdds(m.american, store.settings.oddsFormat)}</Mono></Td>
                      <Td><Mono className="text-ink-faint">{fmtUtc(m.priceObservedAt)}</Mono></Td>
                      <Td><Mono className="text-ink-faint">{fmtUtc(m.enteredAt)}</Mono></Td>
                      <Td>
                        <Mono className={age > 60 ? 'text-bad' : age > 45 ? 'text-warn' : 'text-ok'}>
                          {fmtAgo(m.enteredAt, ds.demoNow)}
                        </Mono>
                      </Td>
                      <Td>{m.confirmedVisible ? <Pill tone="ok">confirmed</Pill> : <Pill tone="bad">unconfirmed</Pill>}</Td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
