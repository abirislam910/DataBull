/**
 * Every server-state read and write in the app.
 *
 * All server state goes through TanStack Query — `useEffect` fetching is not
 * permitted (SPEC § Frontend). Keys are centralised in `queryKeys` so that
 * invalidating after a mutation cannot drift from the key a hook subscribes to.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'
import { ApiError, apiFetch } from './api'
import type {
  AggregateBucket,
  AggregateFn,
  AggregateWindow,
  Alert,
  Device,
  DeviceCreate,
  DeviceUpdate,
  Reading,
} from './types'

export const queryKeys = {
  devices: ['devices'] as const,
  device: (id: string) => ['devices', id] as const,
  readings: (deviceId: string, start?: string, end?: string, limit?: number) =>
    ['readings', deviceId, start ?? null, end ?? null, limit ?? null] as const,
  aggregate: (deviceId: string, window: AggregateWindow, fn: AggregateFn, start?: string) =>
    ['readings', 'aggregate', deviceId, window, fn, start ?? null] as const,
  alerts: (since: string, deviceId?: string) =>
    ['readings', 'alerts', since, deviceId ?? null] as const,
}

/**
 * Polling interval for live telemetry.
 *
 * SPEC's non-goals rule out WebSockets: "polling at 5–10s is sufficient".
 */
export const POLL_INTERVAL_MS = 10_000

/** A 401 means the in-memory token is gone — retrying cannot fix that. */
function retryUnlessUnauthorized(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.isUnauthorized) return false
  return failureCount < 2
}

export function useDevices(): UseQueryResult<Device[], Error> {
  return useQuery({
    queryKey: queryKeys.devices,
    queryFn: () => apiFetch<Device[]>('/devices'),
    retry: retryUnlessUnauthorized,
  })
}

/**
 * Guard for every device-scoped query.
 *
 * An empty id is not a query worth making: the API rejects it as a malformed
 * UUID, and because these hooks poll, an unguarded call turns into a permanent
 * stream of failing requests rather than a single visible error.
 */
function hasDeviceId(deviceId: string): boolean {
  return deviceId !== ''
}

export function useDevice(deviceId: string): UseQueryResult<Device, Error> {
  return useQuery({
    queryKey: queryKeys.device(deviceId),
    queryFn: () => apiFetch<Device>(`/devices/${deviceId}`),
    enabled: hasDeviceId(deviceId),
    retry: retryUnlessUnauthorized,
  })
}

export function useReadings(
  deviceId: string,
  options: { start?: string; end?: string; limit?: number } = {},
): UseQueryResult<Reading[], Error> {
  const { start, end, limit } = options
  return useQuery({
    queryKey: queryKeys.readings(deviceId, start, end, limit),
    queryFn: () =>
      apiFetch<Reading[]>('/readings', {
        params: { device_id: deviceId, start, end, limit },
      }),
    enabled: hasDeviceId(deviceId),
    refetchInterval: POLL_INTERVAL_MS,
    retry: retryUnlessUnauthorized,
  })
}

export function useAggregate(
  deviceId: string,
  window: AggregateWindow,
  fn: AggregateFn,
  start?: string,
): UseQueryResult<AggregateBucket[], Error> {
  return useQuery({
    queryKey: queryKeys.aggregate(deviceId, window, fn, start),
    queryFn: () =>
      apiFetch<AggregateBucket[]>('/readings/aggregate', {
        params: { device_id: deviceId, window, fn, start },
      }),
    enabled: hasDeviceId(deviceId),
    refetchInterval: POLL_INTERVAL_MS,
    retry: retryUnlessUnauthorized,
  })
}

export function useAlerts(since: string, deviceId?: string): UseQueryResult<Alert[], Error> {
  return useQuery({
    queryKey: queryKeys.alerts(since, deviceId),
    queryFn: () =>
      apiFetch<Alert[]>('/readings/alerts', {
        params: { since, device_id: deviceId },
      }),
    refetchInterval: POLL_INTERVAL_MS,
    retry: retryUnlessUnauthorized,
  })
}

export function useCreateDevice(): UseMutationResult<Device, Error, DeviceCreate> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: DeviceCreate) => apiFetch<Device>('/devices', { method: 'POST', body }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.devices })
    },
  })
}

export function useUpdateDevice(deviceId: string): UseMutationResult<Device, Error, DeviceUpdate> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: DeviceUpdate) =>
      apiFetch<Device>(`/devices/${deviceId}`, { method: 'PATCH', body }),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKeys.device(deviceId), updated)
      void queryClient.invalidateQueries({ queryKey: queryKeys.devices })
    },
  })
}

export function useDeleteDevice(): UseMutationResult<void, Error, string> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (deviceId: string) => apiFetch<void>(`/devices/${deviceId}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.devices })
    },
  })
}
