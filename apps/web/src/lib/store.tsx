import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from 'react';
import {
  DEFAULT_RISK_CONTROLS,
  type AppMode,
  type Bet,
  type ManualBookPrice,
  type Recommendation,
  type UserSettings,
} from '@fde/shared-types';
import {
  correctSettlement,
  placeBet,
  settleBet,
  type LedgerState,
  type SettleBetInput,
} from '@fde/calculations';
import type { DemoDataset } from '@fde/api-client';

/**
 * Client state: user settings + the append-only paper-bet ledger + locally
 * entered manual prices. Persisted to localStorage (single-user desktop PWA).
 * In the Supabase deployment this state lives in RLS-protected tables; the
 * shapes here mirror those tables 1:1.
 */

const STORE_KEY = 'fde-store-v1';

export interface StoreState {
  settings: UserSettings;
  ledger: LedgerState | null;
  localManualPrices: ManualBookPrice[];
  ledgerSeeded: boolean;
}

const DEFAULT_SETTINGS: UserSettings = {
  userId: 'demo-user',
  oddsFormat: 'AMERICAN',
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  mode: 'PAPER',
  riskControls: DEFAULT_RISK_CONTROLS,
};

interface StoreContextValue extends StoreState {
  seedLedger: (ds: DemoDataset) => void;
  placePaperBet: (rec: Recommendation, stake: number, nowIso: string, mode: AppMode) => { error?: string };
  settle: (input: SettleBetInput) => { error?: string };
  correct: (betId: string, newResult: 'WIN' | 'LOSS' | 'PUSH', nowIso: string, reason: string) => { error?: string };
  addManualPrice: (p: ManualBookPrice) => void;
  updateSettings: (patch: Partial<UserSettings>) => void;
  exportData: () => void;
  resetData: () => void;
  openBets: Bet[];
  openStake: number;
  weeklyExposurePct: number;
  gameExposure: (gameId: string) => number;
}

const StoreContext = createContext<StoreContextValue | null>(null);

function load(): StoreState {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as StoreState;
      return {
        ...parsed,
        settings: { ...DEFAULT_SETTINGS, ...parsed.settings, riskControls: { ...DEFAULT_RISK_CONTROLS, ...parsed.settings?.riskControls } },
      };
    }
  } catch {
    // fall through to defaults
  }
  return { settings: DEFAULT_SETTINGS, ledger: null, localManualPrices: [], ledgerSeeded: false };
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<StoreState>(load);

  useEffect(() => {
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
  }, [state]);

  const seedLedger = useCallback((ds: DemoDataset) => {
    setState((s) => {
      if (s.ledgerSeeded && s.ledger) return s;
      return {
        ...s,
        ledgerSeeded: true,
        ledger: {
          bets: ds.bets,
          betEvents: ds.betEvents,
          auditLog: ds.auditLog,
          bankrollBalance: ds.bankrollAccount.currentBalance,
        },
      };
    });
  }, []);

  const placePaperBet = useCallback(
    (rec: Recommendation, stake: number, nowIso: string, mode: AppMode) => {
      let error: string | undefined;
      setState((s) => {
        if (!s.ledger) return s;
        try {
          const ledger = placeBet(s.ledger, {
            userId: s.settings.userId,
            bankrollAccountId: 'bank_demo',
            gameId: rec.gameId,
            market: rec.market,
            selection: rec.selection,
            line: rec.line,
            american: rec.american,
            stake,
            mode,
            placedAt: nowIso,
            recommendationId: rec.id,
            predictionId: rec.predictionId,
            modelVersionId: 'mv_ensemble',
            featureSnapshotId: `fsnap_${rec.gameId}_PRACTICE_UPDATE`,
          });
          return { ...s, ledger };
        } catch (e) {
          error = e instanceof Error ? e.message : String(e);
          return s;
        }
      });
      return { error };
    },
    [],
  );

  const settle = useCallback((input: SettleBetInput) => {
    let error: string | undefined;
    setState((s) => {
      if (!s.ledger) return s;
      try {
        return { ...s, ledger: settleBet(s.ledger, input) };
      } catch (e) {
        error = e instanceof Error ? e.message : String(e);
        return s;
      }
    });
    return { error };
  }, []);

  const correct = useCallback((betId: string, newResult: 'WIN' | 'LOSS' | 'PUSH', nowIso: string, reason: string) => {
    let error: string | undefined;
    setState((s) => {
      if (!s.ledger) return s;
      try {
        return { ...s, ledger: correctSettlement(s.ledger, betId, newResult, nowIso, s.settings.userId, reason) };
      } catch (e) {
        error = e instanceof Error ? e.message : String(e);
        return s;
      }
    });
    return { error };
  }, []);

  const addManualPrice = useCallback((p: ManualBookPrice) => {
    // Append-only: never mutate or replace an existing price record.
    setState((s) => ({ ...s, localManualPrices: [...s.localManualPrices, p] }));
  }, []);

  const updateSettings = useCallback((patch: Partial<UserSettings>) => {
    setState((s) => ({ ...s, settings: { ...s.settings, ...patch } }));
  }, []);

  const exportData = useCallback(() => {
    const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `fourth-down-edge-export-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, [state]);

  const resetData = useCallback(() => {
    localStorage.removeItem(STORE_KEY);
    setState({ settings: DEFAULT_SETTINGS, ledger: null, localManualPrices: [], ledgerSeeded: false });
  }, []);

  const openBets = useMemo(
    () => state.ledger?.bets.filter((b) => b.result === 'PENDING') ?? [],
    [state.ledger],
  );
  const openStake = useMemo(() => openBets.reduce((a, b) => a + b.stake, 0), [openBets]);
  const weeklyExposurePct = useMemo(
    () => (state.ledger && state.ledger.bankrollBalance > 0 ? openStake / state.ledger.bankrollBalance : 0),
    [openStake, state.ledger],
  );
  const gameExposure = useCallback(
    (gameId: string) => openBets.filter((b) => b.gameId === gameId).reduce((a, b) => a + b.stake, 0),
    [openBets],
  );

  const value: StoreContextValue = useMemo(
    () => ({
      ...state,
      seedLedger, placePaperBet, settle, correct, addManualPrice, updateSettings,
      exportData, resetData, openBets, openStake, weeklyExposurePct, gameExposure,
    }),
    [state, seedLedger, placePaperBet, settle, correct, addManualPrice, updateSettings, exportData, resetData, openBets, openStake, weeklyExposurePct, gameExposure],
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): StoreContextValue {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error('useStore must be used within StoreProvider');
  return ctx;
}
