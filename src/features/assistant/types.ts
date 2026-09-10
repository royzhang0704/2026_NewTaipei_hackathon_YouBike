/* 調度助理 —— 前端與後端對話端點共用的型別契約。
   後端端點（預留，實作細節見文件）：

     POST /api/v1/assistant/chat        Accept: text/event-stream
     req  { messages: WireMessage[], context: AssistantContext }
     SSE  data: { type:'delta',       text:string }
          data: { type:'sources',     items:AssistantSource[] }
          data: { type:'actions',     items:AssistantAction[] }
          data: { type:'list',        title:string, items:AssistantListItem[] }
          data: { type:'table',       title:string, columns:AssistantTableCol[], rows:AssistantTableRow[] }
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

/** 清單題（哪些站要補車 / 有哪些滿站…）的一列：後端從資料包確定性組出，數字不經 LLM */
export interface AssistantListItem {
  name: string
  /** 右側標籤，如「補 9 台」「高風險・取 3 台」 */
  meta?: string
  /** 站 uid；有值 → 該列可點，行為同 select_station */
  uid?: string
}

/** 多區比較表格（後端從資料包確定性組出，數字不經 LLM） */
export interface AssistantTableCol {
  key: string
  label: string
  align?: 'left' | 'right'
}
export interface AssistantTableRow {
  /** 列首（行政區名）*/
  name: string
  /** town_code；有值 → 點該列切到該區 */
  town?: string
  /** 對應 columns 的字串值 */
  cells: string[]
}

export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  sources?: AssistantSource[]
  actions?: AssistantAction[]
  /** 清單題的結構化清單（標題 + 可點列）；答案文字只寫一句總結，站名/台數看這裡 */
  list?: { title: string; items: AssistantListItem[] }
  /** 多區比較表格；答案文字只寫一句總結，數字看這裡 */
  table?: { title: string; columns: AssistantTableCol[]; rows: AssistantTableRow[] }
  /** 後續建議問題；僅顯示於最後一則助理訊息下方 */
  suggestions?: string[]
  /** 串流尚未收尾 */
  pending?: boolean
  /** 這則回答失敗（顯示重試提示用） */
  failed?: boolean
  /** 建立時間 epoch ms（持久化排序用；顯示改用 virtualAt） */
  at?: number
  /** 建立時的（虛擬）時鐘字串，時間戳顯示用 —— demo 回放時跟隨虛擬時鐘 */
  virtualAt?: string | null
  /** 這則回答對應的使用者問句（重試 / 以最新資料重問用） */
  query?: string
  /** 產生這則回答時的批次時刻（current_slot 字串）——換了新批次才提示「以最新資料重問」 */
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
  /** 每個對話一組（前端 mint、換新對話就換）——後端拿去當 AgentCore runtimeSessionId */
  thread_id?: string
}

/** mock 與 live（SSE）共用的串流回呼 */
export interface ChatHandlers {
  onDelta?: (text: string) => void
  onSources?: (s: AssistantSource[]) => void
  onActions?: (a: AssistantAction[]) => void
  onList?: (l: { title: string; items: AssistantListItem[] }) => void
  onTable?: (t: { title: string; columns: AssistantTableCol[]; rows: AssistantTableRow[] }) => void
  onSuggestions?: (s: string[]) => void
  signal?: AbortSignal
}

export interface ChatResult {
  content: string
  sources?: AssistantSource[]
  actions?: AssistantAction[]
  list?: { title: string; items: AssistantListItem[] }
  table?: { title: string; columns: AssistantTableCol[]; rows: AssistantTableRow[] }
  suggestions?: string[]
}
