import { americanToDecimal } from '@fde/calculations';
import type { OddsFormat } from '@fde/shared-types';

/** Consistent presentation helpers. All persistent storage is UTC. */

export function fmtOdds(american: number, format: OddsFormat): string {
  if (format === 'DECIMAL') return americanToDecimal(american).toFixed(2);
  return american > 0 ? `+${american}` : `${american}`;
}

export function fmtLine(line: number | undefined): string {
  if (line === undefined) return '—';
  if (line === 0) return 'PK';
  return line > 0 ? `+${line}` : `${line}`;
}

/** Market-aware line formatting: totals carry no sign. */
export function fmtMarketLine(market: string, line: number | undefined): string {
  if (line === undefined) return '—';
  if (market === 'TOTAL') return `${line}`;
  return fmtLine(line);
}

/** Probabilities display at one decimal percentage point by default. */
export function fmtPct(p: number, decimals = 1): string {
  return `${(p * 100).toFixed(decimals)}%`;
}

export function fmtSignedPct(p: number, decimals = 1): string {
  const v = p * 100;
  return `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}%`;
}

export function fmtMoney(x: number): string {
  return x.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

export function fmtSignedMoney(x: number): string {
  return `${x >= 0 ? '+' : '−'}${Math.abs(x).toLocaleString('en-US', { style: 'currency', currency: 'USD' })}`;
}

export function fmtUtc(iso: string): string {
  return `${iso.slice(0, 16).replace('T', ' ')}Z`;
}

export function fmtKickoff(iso: string, timezone: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      weekday: 'short', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit', timeZone: timezone, timeZoneName: 'short',
    }).format(new Date(iso));
  } catch {
    return fmtUtc(iso);
  }
}

/** Age relative to the dataset's frozen demo clock. */
export function fmtAgo(iso: string, nowIso: string): string {
  const mins = Math.round((new Date(nowIso).getTime() - new Date(iso).getTime()) / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const h = Math.floor(mins / 60);
  if (h < 48) return `${h}h ${mins % 60}m ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/**
 * A captured instant, in the reader's own timezone.
 *
 * The live screens were printing the stored value verbatim —
 * "2026-08-08T03:24:33.941758+00:00" — which is precise, auditable, and
 * unreadable. Six of those in a table is a wall. The exact UTC string is
 * kept as the `title` wherever this is used, so nothing is lost: the
 * screen becomes legible and the audit value stays one hover away.
 */
export function fmtInstant(iso: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

/**
 * Age against the real wall clock.
 *
 * `fmtAgo` takes an explicit "now" because the demo dataset is frozen and
 * must not drift. The live screens have no frozen clock — staleness there
 * is the actual signal — so they get a separate function rather than a
 * caller-supplied `new Date().toISOString()` that would silently read as
 * demo behaviour.
 */
export function fmtAgoLive(iso: string, now: Date = new Date()): string {
  const ms = now.getTime() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return '—';
  // A future timestamp is a real condition (a scheduled kickoff), not an
  // error, and "-3m ago" is nonsense. Say which direction it points.
  if (ms < 0) return `in ${humanSpan(-ms)}`;
  const mins = Math.round(ms / 60_000);
  if (mins < 1) return 'just now';
  return `${humanSpan(ms)} ago`;
}

function humanSpan(ms: number): string {
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${Math.max(mins, 1)}m`;
  const h = Math.floor(mins / 60);
  if (h < 48) return `${h}h ${mins % 60}m`;
  return `${Math.floor(h / 24)}d`;
}

export function fmtNum(x: number, decimals = 1): string {
  return x.toFixed(decimals);
}

export function fmtSigned(x: number, decimals = 1): string {
  return `${x >= 0 ? '+' : ''}${x.toFixed(decimals)}`;
}
