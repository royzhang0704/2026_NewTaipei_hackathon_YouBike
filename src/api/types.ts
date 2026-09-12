/* 後端回應型別 —— 依 FastAPI 實際回應手寫（OpenAPI 未宣告 response model）。
   對照樣本：GET /api/v1/healthz、/api/v1/towns、/api/v1/stations、
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
  /** demo 回放流速；1 = 即時，0 = 停表，null = 非 demo。
      ★ 2026-09-12 起取代 demo_tail_speed —— 流速搬進 DB，來源只剩一個
        （後端 sys_config.demo_speed）。調度只在 ≤1 開放（見 StationDetail）。 */
  demo_speed?: number | null
  /** 非 null = 後端正在現算這一格的預測 → 前端上全頁遮罩。
      ★ 後端有 TTL 自癒（迴圈被 kill 之後會自己回 null），前端不必再設逾時。 */
  predicting_origin?: string | null
  /** 開始現算的**真實**時間（診斷用，畫面不顯示）。 */
  predicting_since?: string | null
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

/** 連續高風險的輪數／時數——9/12 起只算高風險，中低風險 since/hours 恆為 null
    （n 仍會是 0，物件本身一定存在，不要用 `it.streak &&` 判斷，要看 `it.streak?.hours != null`）。 */
export interface AlertStreak {
  n: number
  since: string | null
  hours: number | null
}

/** 空站建議調車來源——city-wide 找最近的可調出滿站（5km 內），沒有就是 null（改由調度中心備車補入）。
    只有 side==='shortage' 的 item 才會有值；滿站本身就是調出點，沒有這個欄位。 */
export interface AlertDonor {
  station_uid: string
  name: string
  town: string
  bikes: number
  dist_m: number
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
  donor?: AlertDonor | null
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

/* ── 調度單（meet/20260912/計劃-調度確認.md §5）────────────────
   from/to 是**車的方向**；哪一端是風險站看 anchor_uid —— 兩端都可能有燈
   （候選站本身是滿站高風險時，一趟車解決兩站）。 */
export type DispatchStatus = 'active' | 'fulfilled' | 'invalid'

export interface DispatchEnd {
  uid: string
  name: string | null
  town: string | null
  lat: number | null
  lon: number | null
}

export interface DispatchOrder {
  id: number
  action: 'refill' | 'remove' // 站在 anchor 的立場
  bikes: number
  anchor_uid: string
  status: DispatchStatus
  from: DispatchEnd
  to: DispatchEnd
  distance_m: number | null
  created_slot: string // ★ 虛擬時鐘，不是真實時間
  operator: string // ★ 不可信，一律 IM_TEST（後端寫死）
}

export interface DispatchOrdersResponse {
  origin: string | null
  items: DispatchOrder[]
}

/** 候選站（助理 SSE {type:'dispatch'} 與 GET /dispatch/candidates 共用）。 */
export interface DispatchCandidate {
  uid: string
  name: string
  town: string
  cross_town: boolean
  supply: number // 這站能安全給出的最多台數（已扣其他 active 單的承諾）
  distance_m: number | null
  bikes: number // 本筆建議台數
  selected: boolean // 預設勾選
  level: 'high' | 'mid' | 'low' | 'none'
  note: string | null
}

export interface DispatchAnchor {
  uid: string
  name: string
  town: string
  action: 'refill' | 'remove'
  need: number // 還缺幾台 = 本輪建議台數 − 已派出的 active 承諾
  already: number
}

export interface DispatchCandidates {
  origin: string
  anchor: DispatchAnchor
  items: DispatchCandidate[]
  shortfall: number // > 0 = 湊不滿，差額要調度中心備用車
}

export interface DispatchCreated {
  written: number
  origin: string
  ids: number[]
}

export interface ApiError {
  code: string
  message: string
}
