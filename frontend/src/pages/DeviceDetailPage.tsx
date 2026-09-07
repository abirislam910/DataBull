/**
 * Device detail: time-series chart, threshold indicators, alert history
 * (SPEC § Frontend § Scope).
 */
import { Activity, ArrowLeft, BellOff } from 'lucide-react'
import { useMemo } from 'react'
import { Link, useParams } from 'react-router-dom'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAlerts, useDevice, useReadings } from '@/lib/queries'
import type { Device } from '@/lib/types'

const DAY_MS = 24 * 60 * 60 * 1000
const READING_LIMIT = 500

function ThresholdSummary({ device }: { device: Device }) {
  const { min_threshold: min, max_threshold: max } = device
  if (min === null && max === null) {
    return <Badge variant="neutral">No thresholds configured</Badge>
  }
  return (
    <span className="flex items-center gap-2">
      {min !== null ? (
        <Badge variant="warn">
          min <span className="ml-1 font-mono">{min}</span>
        </Badge>
      ) : null}
      {max !== null ? (
        <Badge variant="alert">
          max <span className="ml-1 font-mono">{max}</span>
        </Badge>
      ) : null}
    </span>
  )
}

export function DeviceDetailPage(): JSX.Element {
  const { deviceId = '' } = useParams<{ deviceId: string }>()
  const since = useMemo(() => new Date(Date.now() - DAY_MS).toISOString(), [])

  const device = useDevice(deviceId)
  const readings = useReadings(deviceId, { limit: READING_LIMIT })
  const alerts = useAlerts(since, deviceId)

  // The API returns newest-first (so a truncating limit keeps recent data);
  // a time axis has to be plotted oldest-first.
  const chartData: ChartPoint[] = useMemo(
    () =>
      (readings.data ?? [])
        .map((reading) => ({ time: new Date(reading.time).getTime(), value: reading.value }))
        .sort((a, b) => a.time - b.time),
    [readings.data],
  )

  if (device.isError) {
    return <ErrorState error={device.error} onRetry={() => void device.refetch()} />
  }

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <Button asChild variant="ghost" size="sm">
          <Link to="/devices">
            <ArrowLeft className="h-4 w-4" aria-hidden />
            All devices
          </Link>
        </Button>

        {device.isPending ? (
          <Skeleton className="h-9 w-64" />
        ) : (
          <div className="flex flex-wrap items-center gap-4">
            <h1 className="text-page-title font-semibold text-text">{device.data.name}</h1>
            <Badge variant="neutral">{device.data.type}</Badge>
            <span className="font-mono text-chrome text-text-secondary">{device.data.unit}</span>
            <ThresholdSummary device={device.data} />
          </div>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Readings</CardTitle>
          <p className="text-chrome text-text-secondary">
            Most recent {READING_LIMIT} readings. Dashed lines mark configured thresholds.
          </p>
        </CardHeader>
        <CardContent className="-ml-12">
          {readings.isPending || device.isPending ? (
            <ChartSkeleton className="h-[500px]" />
          ) : readings.isError ? (
            <ErrorState error={readings.error} onRetry={() => void readings.refetch()} />
          ) : chartData.length === 0 ? (
            <EmptyState icon={Activity} message="No readings recorded for this device yet." />
          ) : (
            <ReadingsChart
              data={chartData}
              unit={device.data.unit}
              minThreshold={device.data.min_threshold}
              maxThreshold={device.data.max_threshold}
              height={500}
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Alert history — last 24 hours</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {alerts.isPending ? (
            <TableSkeleton rows={4} columns={3} />
          ) : alerts.isError ? (
            <ErrorState error={alerts.error} onRetry={() => void alerts.refetch()} />
          ) : alerts.data.length === 0 ? (
            <EmptyState icon={BellOff} message="No threshold breaches in the last 24 hours." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Value</TableHead>
                  <TableHead>Breach</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {alerts.data.map((alert) => (
                  <TableRow key={alert.time}>
                    <TableCell className="font-mono text-text-secondary">
                      {new Date(alert.time).toLocaleString()}
                    </TableCell>
                    <TableCell className="font-mono text-text">
                      {alert.value.toFixed(2)} {alert.unit}
                    </TableCell>
                    <TableCell>
                      <Badge variant={alert.bound === 'max' ? 'alert' : 'warn'}>
                        {alert.bound === 'max' ? 'above' : 'below'}{' '}
                        <span className="ml-1 font-mono">{alert.threshold}</span>
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
