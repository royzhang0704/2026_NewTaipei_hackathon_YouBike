import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { api } from './client'
import { qk } from './queryKeys'
import type {
  AlertsResponse,
  DispatchCandidates,
  DispatchCreated,
  DispatchOrdersResponse,
  Health,
  Station,
  StationDay,
  Town,
} from './types'

/* 快取策略：
   - towns / stations：開頁抓一次，長 staleTime；僅在 error 狀態下每 15s 重試，
     成功即停 —— 後端在初次載入時掛掉、之後恢復時能自動補上，不需重整
   - health：每 8s 重抓（頂欄時鐘）
   - alerts / day：批次預測每 30 分更新，staleTime 60s */

// 靜態資料：平時不輪詢；載入失敗時每 15s 重試直到成功
const retryWhileError = (q: { state: { status: string } }) =>
  q.state.status === 'error' ? 15_000 : (false as const)

export function useHealth() {
  return useQuery({
    queryKey: qk.health,
    queryFn: ({ signal }) => api<Health>('/api/v1/healthz', { signal }),
    // 短間隔：頭欄時鐘靠連續兩次 now 推算流速，後端調速度最多 8 秒就跟上
    refetchInterval: 8_000,
    staleTime: 6_000,
  })
}

export function useTowns() {
  return useQuery({
    queryKey: qk.towns,
    queryFn: ({ signal }) => api<Town[]>('/api/v1/towns', { signal }),
    staleTime: 60 * 60_000,
    refetchInterval: retryWhileError,
  })
}

export function useStations() {
  return useQuery({
    queryKey: qk.stations,
    queryFn: ({ signal }) => api<Station[]>('/api/v1/stations', { signal }),
    staleTime: 60 * 60_000,
    refetchInterval: retryWhileError,
  })
}

export function useStationDay(uid: string | null | undefined) {
  return useQuery({
    queryKey: qk.stationDay(uid),
    queryFn: ({ signal }) =>
      api<StationDay>(`/api/v1/stations/${encodeURIComponent(uid!)}/day`, { signal }),
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
    queryFn: ({ signal }) => {
      const qs = new URLSearchParams()
      if (params.town_code) qs.set('town_code', params.town_code)
      if (params.level) qs.set('level', params.level)
      if (params.side) qs.set('side', params.side)
      if (params.action) qs.set('action', params.action)
      qs.set('limit', String(params.limit ?? 100))
      qs.set('offset', String(params.offset ?? 0))
      return api<AlertsResponse>(`/api/v1/alerts?${qs}`, { signal })
    },
    placeholderData: keepPreviousData,
    refetchInterval: 25_000, // 主要靠 useSlotSync 在虛擬時鐘跨格時 invalidate；這是備援
    staleTime: 10_000,
  })
}

/* ── 調度單 ───────────────────────────────────────────────
   ★ 一次全撈、不吃 town_code：跨區調度的兩端分屬不同區，用區篩掉任一端
     線就斷了。區的切換純前端（體例同 useStations）。 */
export function useDispatchOrders() {
  return useQuery({
    queryKey: qk.dispatchOrders,
    queryFn: ({ signal }) =>
      api<DispatchOrdersResponse>('/api/v1/dispatch/orders?status=active', { signal }),
    refetchInterval: 25_000, // 主要靠 useSlotSync 在虛擬時鐘跨格時 invalidate；這是備援
    staleTime: 10_000,
  })
}

/** 某風險站的調度候選。助理按鈕走 SSE，這支給直查／重新整理候選用。 */
export function useDispatchCandidates(uid: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: ['dispatch-candidates', uid],
    queryFn: ({ signal }) =>
      api<DispatchCandidates>(`/api/v1/dispatch/candidates/${encodeURIComponent(uid!)}`, {
        signal,
      }),
    enabled: !!uid && enabled,
    staleTime: 10_000,
  })
}

export interface DispatchCreateBody {
  anchor_uid: string
  action: 'refill' | 'remove'
  items: { uid: string; bikes: number }[]
}

/** 確認調度。★ 失敗整批 400（DISPATCH_STALE），錯誤訊息直接用後端的，不自行改寫。 */
export function useCreateDispatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: DispatchCreateBody) =>
      api<DispatchCreated>('/api/v1/dispatch/orders', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.dispatchOrders })
      qc.invalidateQueries({ queryKey: ['dispatch-candidates'] })
      // 行動卡的「已調度 N 台」由 day 視圖旁的單子算，一併更新
      qc.invalidateQueries({ queryKey: ['station-day'] })
    },
  })
}

/** 人工撤銷 → status='invalid'（軟刪，後端不 DELETE 列）。 */
export function useCancelDispatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      api<{ cancelled: number; id: number }>(`/api/v1/dispatch/orders/${id}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.dispatchOrders })
      qc.invalidateQueries({ queryKey: ['dispatch-candidates'] })
    },
  })
}
