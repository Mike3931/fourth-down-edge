import type {
  FeedStatus,
  Game,
  ManualBookPrice,
  ModelVersion,
  Prediction,
  Recommendation,
} from '@fde/shared-types';
import { generateDemoDataset, type DemoDataset } from './dataset';
import { evaluateCandidates, evaluateGame, type CandidateEvaluation, type EvaluationContext } from './evaluate';

/**
 * Typed API abstraction for Fourth Down Edge.
 *
 * The web app depends only on this interface. Version 1 ships MockFdeApi
 * (deterministic demonstration data computed in the browser and clearly
 * labeled). The future Python FastAPI analytical service implements the same
 * OpenAPI contract (docs/api/openapi.yaml); swapping implementations must not
 * require UI changes. Proprietary model logic must NOT live permanently in
 * the browser — everything in the mock layer is a temporary, clearly isolated
 * placeholder.
 */
export interface FdeApi {
  getDataset(): Promise<DemoDataset>;
  getGames(): Promise<Game[]>;
  getPredictions(gameId: string): Promise<Prediction[]>;
  getRecommendation(gameId: string, ctx?: EvaluationContext): Promise<Recommendation>;
  getAllRecommendations(ctx?: EvaluationContext): Promise<Recommendation[]>;
  getCandidates(gameId: string): Promise<CandidateEvaluation[]>;
  getModelVersions(): Promise<ModelVersion[]>;
  getFeedStatuses(): Promise<FeedStatus[]>;
  /** Append-only: returns the new immutable record. Never overwrites. */
  submitManualPrice(input: Omit<ManualBookPrice, 'id'>): Promise<ManualBookPrice>;
}

const LATENCY_MS = 120;

function delay<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), LATENCY_MS));
}

/**
 * User-entered manual prices survive reloads the way they would against a
 * real backend: the mock persists them (browser localStorage when available)
 * and re-appends them to the dataset on construction.
 */
const MANUAL_PRICE_STORE_KEY = 'fde-mock-manual-prices-v1';

function loadPersistedManualPrices(): ManualBookPrice[] {
  try {
    if (typeof localStorage === 'undefined') return [];
    const raw = localStorage.getItem(MANUAL_PRICE_STORE_KEY);
    return raw ? (JSON.parse(raw) as ManualBookPrice[]) : [];
  } catch {
    return [];
  }
}

function persistManualPrices(prices: ManualBookPrice[]): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(MANUAL_PRICE_STORE_KEY, JSON.stringify(prices));
  } catch {
    // best-effort persistence only
  }
}

export class MockFdeApi implements FdeApi {
  private ds: DemoDataset;
  private manualCounter = 100;
  private userPrices: ManualBookPrice[];

  constructor(seed?: number) {
    this.userPrices = loadPersistedManualPrices();
    this.manualCounter += this.userPrices.length;
    const base = generateDemoDataset(seed);
    this.ds = { ...base, manualPrices: [...base.manualPrices, ...this.userPrices] };
  }

  getDataset(): Promise<DemoDataset> {
    return delay(this.ds);
  }

  getGames(): Promise<Game[]> {
    return delay(this.ds.games);
  }

  getPredictions(gameId: string): Promise<Prediction[]> {
    return delay(this.ds.predictions.filter((p) => p.gameId === gameId));
  }

  getRecommendation(gameId: string, ctx: EvaluationContext = {}): Promise<Recommendation> {
    return delay(evaluateGame(this.ds, gameId, ctx));
  }

  getAllRecommendations(ctx: EvaluationContext = {}): Promise<Recommendation[]> {
    return delay(this.ds.games.map((g) => evaluateGame(this.ds, g.id, ctx)));
  }

  getCandidates(gameId: string): Promise<CandidateEvaluation[]> {
    return delay(evaluateCandidates(this.ds, gameId));
  }

  getModelVersions(): Promise<ModelVersion[]> {
    return delay(this.ds.modelVersions);
  }

  getFeedStatuses(): Promise<FeedStatus[]> {
    return delay(this.ds.feedStatuses);
  }

  submitManualPrice(input: Omit<ManualBookPrice, 'id'>): Promise<ManualBookPrice> {
    if (!input.confirmedVisible) {
      return Promise.reject(new Error('Manual price must be confirmed as currently visible.'));
    }
    const record: ManualBookPrice = { ...input, id: `mbp_local_${this.manualCounter++}` };
    // Append-only: push a NEW record; prior records are never mutated.
    this.ds = { ...this.ds, manualPrices: [...this.ds.manualPrices, record] };
    this.userPrices = [...this.userPrices, record];
    persistManualPrices(this.userPrices);
    return delay(record);
  }
}
