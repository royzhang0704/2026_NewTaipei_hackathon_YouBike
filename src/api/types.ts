/* 後端回應型別 —— 依 FastAPI 實際回應手寫（OpenAPI 未宣告 response model）。
   對照樣本：GET /healthz、/api/v1/towns、/api/v1/stations、
             /api/v1/stations/{uid}/day、/api/v1/alerts */

export interface Health {
  ok: boolean
  mock: boolean
  endpoint: string
  now: string
  virtual_now: string | null
  scheduler_on: boolean
  current_slot: string | null
  forecast_end: string | null
  data_age_min: number | null
  forecast_left_min: number | null
  last_tick: string | null
  tick_age_min: number | null
}

export interface Town {
  town_code: string
  town: string
  station_count: number
}

export interface Station {
  uid: string
  name: string
  town_code: string
  town: string
  lat: number
  lon: number
  capacity: number | null
  addr: string
  model_known: boolean
  proxy_available: boolean
}

export type RiskLevel = 'none' | 'low' | 'mid' | 'high'
export type RiskSide = 'shortage' | 'full'
export type DispatchAction = 'refill' | 'remove' | 'hold'
export type Confidence = 'none' | 'possible' | 'likely' | 'almost_certain'

export interface RiskSideDetail {
  level: RiskLevel
  onset: string | null
  slots: number
  by_slot: number[]
  now_crossed: boolean
  confidence: Confidence
}

export interface Dispatch {
  action: DispatchAction
  bikes: number
  /** /day 可能帶 urgency/by/hint；/alerts 帶 basis。兩邊都可能缺，取用時判 null。 */
  urgency?: 'low' | 'high' | null
  by?: string | null
  hint?: string | null
  basis?: string | null
}

export interface DayRisk {
  threshold: number
  shortage: RiskSideDetail
  full: RiskSideDetail
  overall: RiskLevel
  baseline: number | null
  dispatch: Dispatch | null
}

export interface ActualPoint {
  at: string
  avail: number | null
  carried?: boolean
}

export interface ForecastPoint {
  at: string
  q19: number
  q50: number
  q90: number
}

export interface TruthPoint {
  at: string
  avail: number | null
}

export interface StationDay {
  station: {
    uid: string
    name: string
    town_code: string
    town: string
    capacity: number | null
  }
  // 該站在虛擬現在之前一格水位都沒有（停站期間，實測 ~11 站）→ 後端回 200 軟狀態：
  // origin / now / risk 皆 null、actual 空陣列、附 actual_missing 說明（不是 422）。
  origin: string | null
  now: {
    at: string
    avail: number | null
    free: number | null
    carried: boolean
  } | null
  risk: DayRisk | null
  actual: ActualPoint[]
  forecast: ForecastPoint[]
  truth: TruthPoint[] | null
  model_job: string | null
  source: string
  /** 有實況、但這站沒被預測到（僅缺預測線） */
  forecast_missing?: string | null
  /** 連實況都沒有（整站在這個時刻查無資料） */
  actual_missing?: string | null
  caveats?: string[] | null
}

export interface AlertStreak {
  n: number
  since: string
  hours: number
}

export interface AlertItem {
  station_uid: string
  name: string
  town_code: string
  town: string
  capacity: number | null
  level: RiskLevel
  side: RiskSide
  threshold: number
  confidence: Confidence
  conflict: string | null
  onset: string | null
  now: { avail: number | null; free: number | null; carried: boolean; crossed: boolean }
  baseline: number | null
  streak: AlertStreak | null
  dispatch: Dispatch | null
}

export interface AlertsSummary {
  stations: number
  high: number
  mid: number
  low: number
  none: number
  no_forecast: number
  refill: { stations: number; bikes: number }
  remove: { stations: number; bikes: number }
  hold: number
}

export interface AlertsResponse {
  origin: string
  algo_ver: string
  model_job: string
  scope: string | null
  summary: AlertsSummary
  items: AlertItem[]
  total: number
  limit: number
  offset: number
  source: string
}

export interface ApiError {
  code: string
  message: string
}
