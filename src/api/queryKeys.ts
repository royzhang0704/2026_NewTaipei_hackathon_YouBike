import type { AlertsParams } from './queries'

/** 快取 key 集中管理，endpoint 變多時不會散。 */
export const qk = {
  health: ['health'] as const,
  towns: ['towns'] as const,
  stations: ['stations'] as const,
  stationDay: (uid: string | null | undefined) => ['station-day', uid] as const,
  alerts: (params: AlertsParams) => ['alerts', params] as const,
}
