import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart,
  ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from 'recharts';
import type { DiscretePoint } from '@fde/calculations';
import type { CalibrationBin } from '@fde/shared-types';

/**
 * Chart components. Each chart has an aria-label and a visually available
 * text alternative supplied by the caller (textSummary) so information is
 * not conveyed by graphics alone.
 */

// Mirrors --color-ink-faint from styles.css. Axis tick labels are real text
// (WCAG AA needs 4.5:1 against the panel); reference-line guides are
// decorative structure (3:1 non-text minimum), which this also clears.
const INK_FAINT = '#828b95';
const AXIS = { stroke: INK_FAINT, fontSize: 10 } as const;
const GRID = { stroke: '#2a3342', strokeDasharray: '2 4' } as const;
const TOOLTIP_STYLE = {
  backgroundColor: '#1b2330',
  border: '1px solid #2a3342',
  borderRadius: 4,
  fontSize: 11,
  fontFamily: 'Consolas, monospace',
} as const;

function ChartFrame({
  label, textSummary, height = 180, children,
}: {
  label: string;
  textSummary: string;
  height?: number;
  children: React.ReactElement;
}) {
  return (
    <figure aria-label={label}>
      <div style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-1 text-[11px] leading-snug text-ink-faint">{textSummary}</figcaption>
    </figure>
  );
}

export function DistributionChart({
  dist, label, textSummary, markLine, tone = '#37b8c8',
}: {
  dist: DiscretePoint[];
  label: string;
  textSummary: string;
  markLine?: number;
  tone?: string;
}) {
  const data = dist.map((p) => ({ x: p.value, p: p.probability * 100 }));
  return (
    <ChartFrame label={label} textSummary={textSummary}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid {...GRID} vertical={false} />
        <XAxis dataKey="x" {...AXIS} tickLine={false} interval={9} />
        <YAxis {...AXIS} tickLine={false} tickFormatter={(v: number) => `${v.toFixed(1)}%`} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(v) => [`${Number(v).toFixed(2)}%`, 'probability']}
          labelFormatter={(l) => `value ${l}`}
          isAnimationActive={false}
        />
        {markLine !== undefined && <ReferenceLine x={Math.round(markLine)} stroke="#d9a13b" strokeDasharray="4 3" />}
        <Bar dataKey="p" fill={tone} isAnimationActive={false} />
      </BarChart>
    </ChartFrame>
  );
}

export function CoverByLineChart({
  points, label, textSummary, currentLine,
}: {
  points: Array<{ line: number; prob: number }>;
  label: string;
  textSummary: string;
  currentLine?: number;
}) {
  const data = points.map((p) => ({ x: p.line, p: p.prob * 100 }));
  return (
    <ChartFrame label={label} textSummary={textSummary}>
      <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="x" {...AXIS} tickLine={false} />
        <YAxis {...AXIS} domain={[0, 100]} tickLine={false} tickFormatter={(v: number) => `${v}%`} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(v) => [`${Number(v).toFixed(1)}%`, 'probability']}
          labelFormatter={(l) => `line ${l}`}
          isAnimationActive={false}
        />
        <ReferenceLine y={50} stroke={INK_FAINT} strokeDasharray="3 3" />
        {currentLine !== undefined && <ReferenceLine x={currentLine} stroke="#d9a13b" strokeDasharray="4 3" />}
        <Line type="monotone" dataKey="p" stroke="#37b8c8" dot={false} strokeWidth={1.5} isAnimationActive={false} />
      </LineChart>
    </ChartFrame>
  );
}

export function ReliabilityChart({
  bins, label, textSummary,
}: {
  bins: CalibrationBin[];
  label: string;
  textSummary: string;
}) {
  const data = bins
    .filter((b) => b.count > 0)
    .map((b) => ({ x: b.meanPredicted * 100, y: b.observedRate * 100, n: b.count }));
  return (
    <ChartFrame label={label} textSummary={textSummary} height={220}>
      <ScatterChart margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
        <CartesianGrid {...GRID} />
        <XAxis type="number" dataKey="x" domain={[0, 100]} {...AXIS} tickLine={false} name="predicted" tickFormatter={(v: number) => `${v}%`} />
        <YAxis type="number" dataKey="y" domain={[0, 100]} {...AXIS} tickLine={false} name="observed" tickFormatter={(v: number) => `${v}%`} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(v, name) => [`${Number(v).toFixed(1)}%`, name === 'x' ? 'predicted' : 'observed']}
          isAnimationActive={false}
        />
        <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]} stroke={INK_FAINT} strokeDasharray="3 3" />
        <Scatter data={data} fill="#8b93d6" isAnimationActive={false} />
      </ScatterChart>
    </ChartFrame>
  );
}

export function CurveChart({
  points, label, textSummary, tone = '#37b8c8', yFormatter = (v: number) => v.toFixed(0), refY,
}: {
  points: Array<{ x: string | number; y: number }>;
  label: string;
  textSummary: string;
  tone?: string;
  yFormatter?: (v: number) => string;
  refY?: number;
}) {
  return (
    <ChartFrame label={label} textSummary={textSummary}>
      <AreaChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
        <defs>
          <linearGradient id={`grad-${tone.replace('#', '')}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={tone} stopOpacity={0.25} />
            <stop offset="100%" stopColor={tone} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid {...GRID} vertical={false} />
        <XAxis dataKey="x" {...AXIS} tickLine={false} minTickGap={30} />
        <YAxis {...AXIS} tickLine={false} tickFormatter={yFormatter} domain={['auto', 'auto']} width={52} />
        <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [yFormatter(Number(v)), '']} isAnimationActive={false} />
        {refY !== undefined && <ReferenceLine y={refY} stroke={INK_FAINT} strokeDasharray="3 3" />}
        <Area type="monotone" dataKey="y" stroke={tone} strokeWidth={1.5} fill={`url(#grad-${tone.replace('#', '')})`} isAnimationActive={false} />
      </AreaChart>
    </ChartFrame>
  );
}
