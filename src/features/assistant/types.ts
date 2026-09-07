/* 調度助理 —— 前端與後端對話端點共用的型別契約。
   後端端點（預留，實作細節見文件）：

     POST /api/v1/assistant/chat        Accept: text/event-stream
     req  { messages: WireMessage[], context: AssistantContext }
     SSE  data: { type:'delta',       text:string }
          data: { type:'sources',     items:AssistantSource[] }
          data: { type:'actions',     items:AssistantAction[] }
          data: { type:'suggestions', items:string[] }
          data: { type:'done' }
          data: { type:'error',       message:string }
     非串流 fallback（Content-Type: application/json）：
          { reply:string, sources?:AssistantSource[], actions?:AssistantAction[], suggestions?:string[] } */

export type ChatRole = 'user' | 'assistant'

export interface AssistantSource {
  title: string
  snippet?: string
  uri?: string
}

/** 可點動作：將「開啟指定站點 / 篩選指定行政區」呈現為訊息下方的按鈕 */
export interface AssistantAction {
  label: string
  type: 'select_station' | 'filter_town'
  /** select_station → 站 uid；filter_town → town_code（空字串＝全部行政區） */
  value: string
}

export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  sources?: AssistantSource[]
  actions?: AssistantAction[]
  /** 後續建議問題；僅顯示於最後一則助理訊息下方 */
  suggestions?: string[]
  /** 串流尚未收尾 */
  pending?: boolean
  /** 這則回答失敗（顯示重試提示用） */
  failed?: boolean
  /** 建立時間 epoch ms（時間戳顯示 / 持久化） */
  at?: number
  /** 這則回答對應的使用者問句（重試 / 以最新資料重問用） */
  query?: string
  /** 產生這則回答時的資料時刻（虛擬時鐘字串）——顯示「依 HH:MM 資料」 */
  dataAt?: string | null
}

/** 送往後端的精簡訊息（不含 id 與 UI 狀態） */
export interface WireMessage {
  role: ChatRole
  content: string
}

/** 目前畫面狀態，供後端貼合使用者當前檢視內容 */
export interface AssistantContext {
  town_code: string | null
  station_uid: string | null
  virtual_now: string | null
}

/** mock 與 live（SSE）共用的串流回呼 */
export interface ChatHandlers {
  onDelta?: (text: string) => void
  onSources?: (s: AssistantSource[]) => void
  onActions?: (a: AssistantAction[]) => void
  onSuggestions?: (s: string[]) => void
  signal?: AbortSignal
}

export interface ChatResult {
  content: string
  sources?: AssistantSource[]
  actions?: AssistantAction[]
  suggestions?: string[]
}
