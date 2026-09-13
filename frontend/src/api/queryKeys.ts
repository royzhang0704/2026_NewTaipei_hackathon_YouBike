import type { AlertsParams } from './queries'

/** 快取 key 集中管理，endpoint 變多時不會散。 */
export const qk = {
  health: ['health'] as const,
  towns: ['towns'] as const,
  stations: ['stations'] as const,
  stationDay: (uid: string | null | undefined) => ['station-day', uid] as const,
  alerts: (params: AlertsParams) => ['alerts', params] as const,
  // 不帶參數：後端不吃 town_code，區的切換純前端（跨區調度線篩掉任一端就斷了）
  dispatchOrders: ['dispatch-orders'] as const,
}
