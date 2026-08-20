/**
 * The one time-series chart in the app.
 *
 * SPEC § Frontend § Charts fixes every colour and stroke here, so the rules are
 * encoded once in this file rather than re-decided at each call site. Chart
 * colours are never chosen ad-hoc: primary series is always `accent`, the max
 * threshold is always `alert` dashed, the min threshold always `warn` dashed.
 *
 * Recharts takes colours as props, not classes, so the token hex values appear
 * as constants here. This is the one sanctioned exception to "no inline hex" —
 * and it is why they are named constants mirroring tailwind.config.ts rather
 * than literals sprinkled through the JSX.
 */
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

/** Mirrors the design tokens in tailwind.config.ts. */
const TOKEN = {
  accent: '#F97316',
  alert: '#EF4444',
  warn: '#F59E0B',
  border: '#252A2F',
  textMuted: '#6B7280',
  surface: '#14171A',
} as const

const MONO_FONT = 'JetBrains Mono, ui-monospace, monospace'

export interface ChartPoint {
  /** Epoch milliseconds — numeric so the axis can scale by real time. */
  time: number
  value: number
}

function formatClockTime(epochMs: number): string {
  return new Date(epochMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function ChartTooltip({
  active,
  payload,
  unit,
}: {
  active?: boolean
  payload?: Array<{ payload: ChartPoint }>
  unit: string
}) {
  const point = payload?.[0]?.payload
  if (active !== true || point === undefined) return null
  return (
    <div className="rounded-md border-t-2 border-t-accent bg-surface px-3 py-2 shadow-lg">
      <p className="font-mono text-cell text-text-muted">
        {new Date(point.time).toLocaleString()}
      </p>
      <p className="font-mono text-chrome text-text">
        {point.value.toFixed(2)} {unit}
      </p>
    </div>
  )
}

export function ReadingsChart({
  data,
  unit,
  minThreshold,
  maxThreshold,
  height = 240,
}: {
  data: ChartPoint[]
  unit: string
  minThreshold?: number | null
  maxThreshold?: number | null
  /** 240px inside dashboard cards, 400px on the device detail page. */
  height?: number
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
        {/* Horizontal lines only, at 20% opacity — the grid orients, it doesn't compete. */}
        <CartesianGrid stroke={TOKEN.border} strokeOpacity={0.2} vertical={false} />
        <XAxis
          dataKey="time"
          type="number"
          domain={['dataMin', 'dataMax']}
          scale="time"
          tickFormatter={formatClockTime}
          tick={{ fill: TOKEN.textMuted, fontFamily: MONO_FONT, fontSize: 12 }}
          stroke={TOKEN.border}
          // Dense telemetry produces far more ticks than fit; without a minimum
          // gap Recharts renders them all and the labels collide into an
          // unreadable smear. It drops ticks to honour this spacing instead.
          minTickGap={48}
        />
        <YAxis
          tick={{ fill: TOKEN.textMuted, fontFamily: MONO_FONT, fontSize: 12 }}
          stroke={TOKEN.border}
          width={56}
        />
        <Tooltip
          content={<ChartTooltip unit={unit} />}
          cursor={{ stroke: TOKEN.accent, strokeOpacity: 0.3 }}
        />
        {typeof maxThreshold === 'number' ? (
          <ReferenceLine y={maxThreshold} stroke={TOKEN.alert} strokeDasharray="4 4" />
        ) : null}
        {typeof minThreshold === 'number' ? (
          <ReferenceLine y={minThreshold} stroke={TOKEN.warn} strokeDasharray="4 4" />
        ) : null}
        <Line
          type="monotone"
          dataKey="value"
          stroke={TOKEN.accent}
          strokeWidth={2}
          // Telemetry series are dense; per-point dots turn into noise. The
          // active dot on hover still gives a precise read.
          dot={false}
          activeDot={{ r: 4, fill: TOKEN.accent }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
