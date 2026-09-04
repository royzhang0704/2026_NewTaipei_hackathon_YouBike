import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { api } from './client'
import { qk } from './queryKeys'
import type { AlertsResponse, Health, Station, StationDay, Town } from './types'

/* 快取策略：
   - towns / stations：開頁抓一次，長 staleTime
   - health：每 30s 重抓（頂欄時鐘）
   - alerts / day：批次預測每 30 分更新，staleTime 60s */

export function useHealth() {
  return useQuery({
    queryKey: qk.health,
    queryFn: () => api<Health>('/healthz'),
    // 短間隔：頭欄時鐘靠連續兩次 now 推算流速，後端調速度最多 8 秒就跟上
    refetchInterval: 8_000,
    staleTime: 6_000,
  })
}

export function useTowns() {
  return useQuery({
    queryKey: qk.towns,
    queryFn: () => api<Town[]>('/api/v1/towns'),
    staleTime: 60 * 60_000,
  })
}

export function useStations() {
  return useQuery({
    queryKey: qk.stations,
    queryFn: () => api<Station[]>('/api/v1/stations'),
    staleTime: 60 * 60_000,
  })
}

export function useStationDay(uid: string | null | undefined) {
  return useQuery({
    queryKey: qk.stationDay(uid),
    queryFn: () => api<StationDay>(`/api/v1/stations/${encodeURIComponent(uid!)}/day`),
    enabled: !!uid,
    // demo 回放時鐘會往前跑，資料要跟著更新
    refetchInterval: 25_000, // 主要靠 useSlotSync 在虛擬時鐘跨格時 invalidate；這是備援
    staleTime: 10_000,
  })
}

export interface AlertsParams {
  town_code?: string | null
  level?: string | null
  side?: string | null
  action?: string | null
  limit?: number
  offset?: number
}

export function useAlerts(params: AlertsParams) {
  return useQuery({
    queryKey: qk.alerts(params),
    queryFn: () => {
      const qs = new URLSearchParams()
      if (params.town_code) qs.set('town_code', params.town_code)
      if (params.level) qs.set('level', params.level)
      if (params.side) qs.set('side', params.side)
      if (params.action) qs.set('action', params.action)
      qs.set('limit', String(params.limit ?? 100))
      qs.set('offset', String(params.offset ?? 0))
      return api<AlertsResponse>(`/api/v1/alerts?${qs}`)
    },
    placeholderData: keepPreviousData,
    refetchInterval: 25_000, // 主要靠 useSlotSync 在虛擬時鐘跨格時 invalidate；這是備援
    staleTime: 10_000,
  })
}
