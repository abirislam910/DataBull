/** Device list + registration. Table over cards, per SPEC's five-or-more rule. */
import { HardDrive, Loader2, Plus, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states/DataStates'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ApiError } from '@/lib/api'
import { useCreateDevice, useDeleteDevice, useDevices } from '@/lib/queries'
import { DEVICE_TYPES, UNIT_BY_TYPE, type Device, type DeviceType } from '@/lib/types'

function ThresholdCell({ device }: { device: Device }) {
  const { min_threshold: min, max_threshold: max } = device
  if (min === null && max === null) {
    return <span className="text-text-muted">—</span>
  }
  return (
    <span className="font-mono">
      {min === null ? '−∞' : min} / {max === null ? '∞' : max}
    </span>
  )
}

function NewDeviceForm({ onDone }: { onDone: () => void }) {
  const createDevice = useCreateDevice()
  const [name, setName] = useState('')
  const [type, setType] = useState<DeviceType>('temperature')
  const [unit, setUnit] = useState(UNIT_BY_TYPE.temperature)

  const error = createDevice.error instanceof ApiError ? createDevice.error : null

  function handleTypeChange(next: DeviceType): void {
    setType(next)
    // Suggest the conventional unit, but leave it editable.
    setUnit(UNIT_BY_TYPE[next])
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    createDevice.mutate(
      { name, type, unit, min_threshold: null, max_threshold: null },
      { onSuccess: onDone },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="grid gap-4 sm:grid-cols-4 sm:items-end">
      <div className="space-y-2">
        <Label htmlFor="device-name">Name</Label>
        <Input
          id="device-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Pump-3"
          required
          aria-invalid={error?.field === 'name'}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="device-type">Type</Label>
        {/* Native select: shadcn's Select is a listbox that needs more wiring
            than a three-option field warrants, and this stays keyboard- and
            screen-reader-native. */}
        <select
          id="device-type"
          value={type}
          onChange={(event) => handleTypeChange(event.target.value as DeviceType)}
          className="flex h-10 w-full rounded-md border border-border bg-bg px-3 text-chrome text-text"
        >
          {DEVICE_TYPES.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-2">
        <Label htmlFor="device-unit">Unit</Label>
        <Input
          id="device-unit"
          value={unit}
          onChange={(event) => setUnit(event.target.value)}
          required
        />
      </div>

      <Button type="submit" disabled={createDevice.isPending}>
        {createDevice.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
        Add device
      </Button>

      {error !== null ? (
        <p role="alert" className="text-cell text-alert sm:col-span-4">
          {error.message}
        </p>
      ) : null}
    </form>
  )
}

export function DevicesPage(): JSX.Element {
  const devices = useDevices()
  const deleteDevice = useDeleteDevice()
  const [isAdding, setIsAdding] = useState(false)

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-page-title font-semibold text-text">Devices</h1>
        <Button size="sm" onClick={() => setIsAdding((open) => !open)}>
          <Plus className="h-4 w-4" aria-hidden />
          {isAdding ? 'Cancel' : 'New device'}
        </Button>
      </div>

      {isAdding ? (
        <Card>
          <CardHeader>
            <CardTitle>Register a device</CardTitle>
          </CardHeader>
          <CardContent>
            <NewDeviceForm onDone={() => setIsAdding(false)} />
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardContent className="p-0">
          {devices.isPending ? (
            <TableSkeleton rows={5} columns={5} />
          ) : devices.isError ? (
            <ErrorState error={devices.error} onRetry={() => void devices.refetch()} />
          ) : devices.data.length === 0 ? (
            <EmptyState
              icon={HardDrive}
              message="No devices yet. Register one to start collecting readings."
              action={
                <Button size="sm" onClick={() => setIsAdding(true)}>
                  <Plus className="h-4 w-4" aria-hidden />
                  Add a device
                </Button>
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Unit</TableHead>
                  <TableHead>Min / Max</TableHead>
                  <TableHead className="w-12" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {devices.data.map((device) => (
                  <TableRow key={device.id}>
                    <TableCell>
                      <Link
                        to={`/devices/${device.id}`}
                        className="text-chrome text-text hover:text-accent"
                      >
                        {device.name}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Badge variant="neutral">{device.type}</Badge>
                    </TableCell>
                    <TableCell className="font-mono text-text-secondary">{device.unit}</TableCell>
                    <TableCell>
                      <ThresholdCell device={device} />
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Delete ${device.name}`}
                        disabled={deleteDevice.isPending}
                        onClick={() => deleteDevice.mutate(device.id)}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden />
                      </Button>
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
