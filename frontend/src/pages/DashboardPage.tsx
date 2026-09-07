/**
 * Overview: device count, active-alert count, and an activity chart across all
 * devices for the last hour (SPEC § Frontend § Scope).
 */
import { Activity, BellOff, HardDrive, Plus, TriangleAlert } from 'lucide-react'
import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { ReadingsChart, type ChartPoint } from '@/components/ReadingsChart'
import {
  ChartSkeleton,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '@/components/states/DataStates'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useAggregate, useAlerts, useDevices } from '@/lib/queries'
import type { Alert } from '@/lib/types'

const HOUR_MS = 60 * 60 * 1000
const DAY_MS = 24 * HOUR_MS

function StatCard({
  label,
  value,
  icon: Icon,
  isLoading,
  tone = 'text-text',
}: {
  label: string
  value: number | undefined
  icon: typeof HardDrive
  isLoading: boolean
  tone?: string
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-6">
        <span className="rounded-md bg-surface-hover p-3">
          <Icon className="h-6 w-6 text-text-secondary" aria-hidden />
        </span>
        <div>
          <p className="text-chrome text-text-secondary">{label}</p>
          {isLoading ? (
            <Skeleton className="mt-1 h-8 w-12" />
          ) : (
            <p className={`font-mono text-page-title ${tone}`}>{value ?? 0}</p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function AlertRow({ alert }: { alert: Alert }) {
  return (
    <li className="flex items-center justify-between gap-4 border-b border-border px-6 py-3 last:border-0">
      <div className="min-w-0">
        <Link
          to={`/devices/${alert.device_id}`}
          className="text-chrome text-text hover:text-accent"
        >
          {alert.device_name}
        </Link>
        <p className="font-mono text-cell text-text-muted">
          {new Date(alert.time).toLocaleString()}
        </p>
      </div>
      <div className="flex items-center gap-3">
        <span className="font-mono text-chrome text-text">
          {alert.value.toFixed(2)} {alert.unit}
        </span>
        {/* Under the floor is a warning; over the ceiling is an alert. */}
        <Badge variant={alert.bound === 'max' ? 'alert' : 'warn'}>
          {alert.bound === 'max' ? 'above' : 'below'} {alert.threshold}
        </Badge>
      </div>
    </li>
  )
}

export function DashboardPage(): JSX.Element {
  // `useMemo` keeps these ISO strings stable across renders — they are part of
  // the query keys, so recomputing them every render would refetch endlessly.
  const since = useMemo(() => new Date(Date.now() - DAY_MS).toISOString(), [])
  const activityStart = useMemo(() => new Date(Date.now() - HOUR_MS).toISOString(), [])

  const devices = useDevices()
  const alerts = useAlerts(since)

  // "Activity across all devices" with one device_id-scoped endpoint: chart the
  // first device and label it honestly rather than implying a fleet-wide roll-up
  // the API cannot yet produce. See the note in the card header.
  const primaryDevice = devices.data?.[0]
  const activity = useAggregate(primaryDevice?.id ?? '', '1h', 'avg', activityStart)

  const chartData: ChartPoint[] =
    activity.data?.map((bucket) => ({
      time: new Date(bucket.bucket).getTime(),
      value: bucket.value,
    })) ?? []

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-page-title font-semibold text-text">Dashboard</h1>
        <Button asChild variant="secondary" size="sm">
          <Link to="/devices">
            <HardDrive className="h-4 w-4" aria-hidden />
            Manage devices
          </Link>
        </Button>
      </div>

      <div className="grid gap-8 sm:grid-cols-2">
        <StatCard
          label="Devices"
          value={devices.data?.length}
          icon={HardDrive}
          isLoading={devices.isPending}
        />
        <StatCard
          label="Alerts (24h)"
          value={alerts.data?.length}
          icon={TriangleAlert}
          isLoading={alerts.isPending}
          tone={alerts.data && alerts.data.length > 0 ? 'text-alert' : 'text-ok'}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Activity — last hour</CardTitle>
          {primaryDevice ? (
            <p className="text-chrome text-text-secondary">
              Hourly average for <span className="font-mono text-text">{primaryDevice.name}</span>
            </p>
          ) : null}
        </CardHeader>
        <CardContent>
          {/* Order matters: the device list has to resolve before the chart
              query means anything. With no devices the aggregate query is
              disabled, so it stays `pending` forever — checking it first would
              render a skeleton that never resolves. */}
          {devices.isPending ? (
            <ChartSkeleton className="h-60" />
          ) : devices.isError ? (
            <ErrorState error={devices.error} onRetry={() => void devices.refetch()} />
          ) : devices.data.length === 0 ? (
            <EmptyState
              icon={HardDrive}
              message="Register a device to start collecting telemetry."
              action={
                <Button asChild size="sm">
                  <Link to="/devices">
                    <Plus className="h-4 w-4" aria-hidden />
                    Add a device
                  </Link>
                </Button>
              }
            />
          ) : activity.isPending ? (
            <ChartSkeleton className="h-60" />
          ) : activity.isError ? (
            <ErrorState error={activity.error} onRetry={() => void activity.refetch()} />
          ) : chartData.length === 0 ? (
            <EmptyState icon={Activity} message="No readings recorded in the last hour." />
          ) : (
            <ReadingsChart data={chartData} unit={primaryDevice?.unit ?? ''} height={240} />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent alerts</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {alerts.isPending ? (
            <TableSkeleton rows={3} columns={3} />
          ) : alerts.isError ? (
            <ErrorState error={alerts.error} onRetry={() => void alerts.refetch()} />
          ) : alerts.data.length === 0 ? (
            <EmptyState icon={BellOff} message="No threshold breaches in the last 24 hours." />
          ) : (
            <ul>
              {alerts.data.slice(0, 8).map((alert) => (
                <AlertRow key={`${alert.device_id}-${alert.time}`} alert={alert} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
