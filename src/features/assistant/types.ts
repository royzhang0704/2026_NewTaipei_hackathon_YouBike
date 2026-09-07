/* AI 調度助理 —— 前端與後端 RAG endpoint 共用的型別契約。
   後端接口（先預留，隊友照這個實作）：

     POST /api/v1/assistant/chat        Accept: text/event-stream
     req  { messages: WireMessage[], context: AssistantContext }
     SSE  data: { type:'delta',   text:string }        // token 逐段
          data: { type:'sources', items:AssistantSource[] }
          data: { type:'actions', items:AssistantAction[] }
          data: { type:'done' }
          data: { type:'error',   message:string }
     非串流 fallback（Content-Type: application/json）：
          { reply:string, sources?:AssistantSource[], actions?:AssistantAction[] }

   分工建議：
   · 知識庫（RAG）＝靜態知識：名詞定義、調度 SOP、風險分級怎麼算、系統操作說明。
   · 即時狀態（哪站高風險 / 某站預測）＝後端回答時直接查現有 service，不進 KB（會過期）。
*/

export type ChatRole = 'user' | 'assistant'

export interface AssistantSource {
  title: string
  snippet?: string
  uri?: string
}

/** 可點動作：把「跳到某站 / 篩某區」變成訊息底下的按鈕 */
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
  /** 串流尚未收尾 */
  pending?: boolean
  /** 這則回答失敗（顯示重試提示用） */
  failed?: boolean
}

/** 送給後端的精簡訊息（不帶 id / UI 狀態） */
export interface WireMessage {
  role: ChatRole
  content: string
}

/** 目前畫面狀態，讓回答貼合使用者在看什麼 */
export interface AssistantContext {
  town_code: string | null
  station_uid: string | null
  virtual_now: string | null
}

/** mock 與 live(SSE) 共用的串流回呼 */
export interface ChatHandlers {
  onDelta?: (text: string) => void
  onSources?: (s: AssistantSource[]) => void
  onActions?: (a: AssistantAction[]) => void
  signal?: AbortSignal
}

export interface ChatResult {
  content: string
  sources?: AssistantSource[]
  actions?: AssistantAction[]
}
