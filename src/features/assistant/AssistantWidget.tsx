import { useCallback, useEffect, useRef, useState } from 'react'
import { useEventListener } from 'usehooks-ts'
import {
  AlertTriangle,
  ArrowDown,
  Bot,
  Check,
  Copy,
  CornerDownLeft,
  MessageSquarePlus,
  RefreshCw,
  Square,
  X,
} from 'lucide-react'
import { useHealth, useStations } from '@/api/queries'
import { useAppStore } from '@/stores/useAppStore'
import { useAssistantStore } from '@/stores/useAssistantStore'
import { segChip } from '@/components/ui/segChip'
import { cn } from '@/lib/utils'
import { sendChat } from './api'
import type { ChatMessage } from './types'

const uid = () => Math.random().toString(36).slice(2, 10)
const STORE_KEY = 'yb_assistant_thread'
const TID_KEY = 'yb_assistant_thread_id'
const MAX_KEEP = 50 // 持久化只留最近 N 則

// 每個對話一組 id：後端當 AgentCore runtimeSessionId（多輪記憶）。
// 換新對話就 mint 新的 → 內容重複也不會撞進舊 session 的記憶。
const newThreadId = () =>
  (globalThis.crypto?.randomUUID?.() ?? `${uid()}${uid()}${uid()}${uid()}${uid()}`)

function loadThreadId(): string {
  try {
    const v = localStorage.getItem(TID_KEY)
    if (v && v.length >= 8) return v
  } catch {
    /* 隱私模式 → 用臨時 id */
  }
  const id = newThreadId()
  try {
    localStorage.setItem(TID_KEY, id)
  } catch {
    /* ignore */
  }
  return id
}

// 空白頁範例問句：依畫面目前選取的站／區套模板（不寫死，也不打 LLM）
function suggestionsFor(townName: string | null, stationName: string | null): string[] {
  if (stationName)
    return [`「${stationName}」要不要補車？`, `「${stationName}」為什麼建議這個台數？`, '現在全市概況？']
  if (townName) return [`${townName}該怎麼調度？`, `${townName}哪幾站最急？`, `${townName}幾站空站？`]
  return ['現在有幾個高風險站？', '哪些站要優先補車？', '空站門檻怎麼定的？']
}

const greeting = (): ChatMessage => ({
  id: 'greeting',
  role: 'assistant',
  content: '可查詢目前的供需、風險分級與待調度站點。點下方範例，或直接輸入問題。',
  at: Date.now(),
})

function loadThread(): ChatMessage[] {
  try {
    const raw = localStorage.getItem(STORE_KEY)
    const parsed = raw ? (JSON.parse(raw) as ChatMessage[]) : null
    // 去掉「上次關頁時還在串流」的殘影狀態
    if (Array.isArray(parsed) && parsed.length) return parsed.map((m) => ({ ...m, pending: false }))
  } catch {
    /* 隱私模式 / 壞資料 → 用預設 */
  }
  return [greeting()]
}

function saveThread(ms: ChatMessage[]) {
  try {
    // 不存還在串流中的殘影；截最後 MAX_KEEP 則
    localStorage.setItem(STORE_KEY, JSON.stringify(ms.filter((m) => !m.pending).slice(-MAX_KEEP)))
  } catch {
    /* 容量滿 / 隱私模式 → 放棄持久化，不影響使用 */
  }
}

const clock = (at?: number) =>
  at ? new Date(at).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false }) : ''

// 時間戳：demo 回放時顯示虛擬時鐘（"YYYY-MM-DD HH:MM:SS" → "HH:MM"），否則用真實時間
const msgTime = (m: ChatMessage) =>
  m.virtualAt && m.virtualAt.length >= 16 ? m.virtualAt.slice(11, 16) : clock(m.at)

// 把（虛擬）時鐘字串 floor 到 30 分鐘批次界線："…13:49:52" → "…13:30"。
// 回放與正式都適用：資料每 30 分換一批，時鐘跨界線＝換批＝該提示重問（不依賴 current_slot / tick）。
const slotOf = (ts?: string | null): string | null => {
  if (!ts || ts.length < 16) return null
  return `${ts.slice(0, 14)}${Number(ts.slice(14, 16)) < 30 ? '00' : '30'}`
}

function isTyping() {
  const el = document.activeElement as HTMLElement | null
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
}

export function AssistantWidget() {
  const open = useAssistantStore((s) => s.open)
  const setOpen = useAssistantStore((s) => s.setOpen)
  const toggle = useAssistantStore((s) => s.toggle)

  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectStation = useAppStore((s) => s.selectStation)
  const selectTown = useAppStore((s) => s.selectTown)
  const setTownQuiet = useAppStore((s) => s.setTownQuiet)

  // stations 僅供點「開啟站點」按鈕時把 uid 換成站名；health 提供資料時刻
  const { data: stations } = useStations()
  const { data: health } = useHealth()

  const [messages, setMessages] = useState<ChatMessage[]>(loadThread)
  const [threadId, setThreadId] = useState<string>(loadThreadId)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [atBottom, setAtBottom] = useState(true)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  // 供報讀者使用的「完成後才播報」鏡像：串流期間不朗讀，回答收尾、失敗或停止時更新一次
  const [announce, setAnnounce] = useState('')

  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fabRef = useRef<HTMLButtonElement>(null)
  const newThreadBtnRef = useRef<HTMLButtonElement>(null)
  const cancelClearRef = useRef<HTMLButtonElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const wasOpen = useRef(false)
  const stickRef = useRef(true) // 使用者是否黏在底部（決定要不要自動捲）
  const messagesRef = useRef(messages)

  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  const patchMsg = useCallback(
    (id: string, patch: (m: ChatMessage) => Partial<ChatMessage>) =>
      setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...patch(m) } : m))),
    [],
  )

  // 送給後端的「現在」用（虛擬）牆上時鐘；判斷答案是否過期用「批次界線」＝時鐘 floor 到 30 分。
  // 每 30 分換一批，跨界線＝資料換了才提示重問；回放與正式皆適用，不依賴 current_slot / tick。
  const dataNow = health?.virtual_now ?? health?.now ?? null
  const curSlot = slotOf(dataNow)

  // 持久化：串流期間逐 token 寫入 localStorage 過於頻繁，故 sending 時略過，收尾（sending 轉 false）再寫入一次
  useEffect(() => {
    if (!sending) saveThread(messages)
  }, [messages, sending])

  // 開／關的焦點管理：開 → 焦點進輸入框；關 → 焦點送回觸發鈕（WCAG 2.4.3），
  // 並中止進行中的請求（Widget 常駐、關閉只是隱藏，不會自然卸載）。
  useEffect(() => {
    if (open) {
      inputRef.current?.focus()
      // 重開時面板 JSX 是全新掛載（scrollTop 歸 0）→ 直接帶到最新一則
      stickRef.current = true
      requestAnimationFrame(() => {
        const el = listRef.current
        if (el) el.scrollTop = el.scrollHeight
      })
    } else if (wasOpen.current) {
      abortRef.current?.abort()
      fabRef.current?.focus()
    }
    wasOpen.current = open
  }, [open])

  // Esc：先收「清除確認」列，再關面板
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (confirmClear) {
        setConfirmClear(false)
        newThreadBtnRef.current?.focus()
      } else {
        setOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setOpen, confirmClear])

  // 確認列出現 → 焦點落在「取消」（安全選項為預設）
  useEffect(() => {
    if (confirmClear) cancelClearRef.current?.focus()
  }, [confirmClear])

  // A：開 / 關助理（未在輸入框時）
  useEventListener('keydown', (e) => {
    if ((e.key === 'a' || e.key === 'A') && !isTyping() && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault()
      toggle()
    }
  })

  // 新訊息 / 串流 → 僅當使用者停留在底部時自動捲動（往上瀏覽歷史時不強制捲回）
  useEffect(() => {
    if (stickRef.current) listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [messages])

  // 卸載時中止進行中的請求
  useEffect(() => () => abortRef.current?.abort(), [])

  const onScroll = () => {
    const el = listRef.current
    if (!el) return
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48
    stickRef.current = bottom
    setAtBottom(bottom)
  }

  const jumpToLatest = () => {
    stickRef.current = true
    setAtBottom(true)
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }

  // 把 wire 對話送出，答案寫進 botId 這則訊息（send 與 retry 共用）
  async function runQuery(wire: { role: 'user' | 'assistant'; content: string }[], botId: string) {
    setSending(true)
    setAnnounce('')
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const ac = new AbortController()
    abortRef.current = ac
    try {
      const res = await sendChat(
        wire,
        {
          town_code: townCode || null,
          station_uid: selectedUid,
          virtual_now: dataNow,
          thread_id: threadId,
        },
        {
          signal: ac.signal,
          // 減少動態偏好：不逐字更新，等收尾一次補上整段
          onDelta: reduce ? undefined : (d) => patchMsg(botId, (m) => ({ content: m.content + d })),
          onSources: (s) => patchMsg(botId, () => ({ sources: s })),
          onActions: (a) => patchMsg(botId, () => ({ actions: a })),
          onList: (l) => patchMsg(botId, () => ({ list: l })),
          onTable: (t) => patchMsg(botId, () => ({ table: t })),
          onSuggestions: (s) => patchMsg(botId, () => ({ suggestions: s })),
        },
      )
      patchMsg(botId, (m) => ({
        content: res.content || m.content || '（無回應）',
        sources: res.sources,
        actions: res.actions,
        list: res.list,
        table: res.table,
        suggestions: res.suggestions,
        pending: false,
        failed: false,
      }))
      setAnnounce('助理已回覆')
    } catch (e) {
      if (ac.signal.aborted) {
        patchMsg(botId, (m) => ({ content: m.content || '已停止。', pending: false }))
        setAnnounce('已停止')
      } else {
        console.error('[assistant] runQuery failed:', e)
        // 後端的 SSE error 事件會帶可讀訊息（繁中）；HTTP/網路錯誤（"assistant 500" 之類）則用罐頭字
        const raw = e instanceof Error ? e.message : ''
        const msg = raw && !/^assistant \d+$/.test(raw) && !/^Failed to fetch/i.test(raw)
          ? raw
          : '調度助理暫時無法回應，請稍後再試。'
        patchMsg(botId, () => ({ content: msg, pending: false, failed: true }))
        setAnnounce(msg)
      }
    } finally {
      setSending(false)
      abortRef.current = null
    }
  }

  function send(text: string) {
    const q = text.trim()
    if (!q || sending) return
    setInput('')
    stickRef.current = true
    const prior = messagesRef.current
      .filter((m) => m.id !== 'greeting' && !m.failed && !m.pending)
      .map((m) => ({ role: m.role, content: m.content }))
    const now = Date.now()
    const botId = uid()
    setMessages((ms) => [
      ...ms,
      { id: uid(), role: 'user', content: q, at: now, virtualAt: dataNow },
      { id: botId, role: 'assistant', content: '', pending: true, at: now, virtualAt: dataNow, query: q, dataAt: curSlot },
    ])
    runQuery([...prior, { role: 'user', content: q }], botId)
  }

  // 就地重試：將該則轉回 pending（不移除、不重新加入），避免範例訊息閃現
  function retry(id: string) {
    if (sending) return
    const idx = messagesRef.current.findIndex((m) => m.id === id)
    const q = idx >= 0 ? messagesRef.current[idx].query : undefined
    if (!q) return
    stickRef.current = true
    const prior = messagesRef.current
      .slice(0, idx) // 該則之前的訊息（已含對應的使用者提問）
      .filter((m) => m.id !== 'greeting' && !m.failed && !m.pending)
      .map((m) => ({ role: m.role, content: m.content }))
    patchMsg(id, () => ({ content: '', pending: true, failed: false, at: Date.now(), virtualAt: dataNow, dataAt: curSlot }))
    runQuery(prior, id)
  }

  function requestNewThread() {
    // 無實質內容時直接開啟新對話，不需確認
    if (!messagesRef.current.some((m) => m.role === 'user')) doClearThread()
    else setConfirmClear(true)
  }

  function doClearThread() {
    abortRef.current?.abort()
    setConfirmClear(false)
    setMessages([greeting()])
    const nextId = newThreadId()
    setThreadId(nextId)
    try {
      localStorage.removeItem(STORE_KEY)
      localStorage.setItem(TID_KEY, nextId)
    } catch {
      /* ignore */
    }
    setAnnounce('已開始新對話')
    inputRef.current?.focus()
  }

  function cancelClear() {
    setConfirmClear(false)
    newThreadBtnRef.current?.focus()
  }

  function copy(m: ChatMessage) {
    navigator.clipboard?.writeText(m.content).then(
      () => {
        setCopiedId(m.id)
        setTimeout(() => setCopiedId((c) => (c === m.id ? null : c)), 1500)
      },
      () => {},
    )
  }

  // 選到某站（action 按鈕與清單列共用）：篩選範圍外的站先靜默切區，鏡頭交給選站的 flyTo
  function openStation(uid: string) {
    const st = stations?.find((s) => s.uid === uid)
    if (st && townCode && st.town_code !== townCode) setTownQuiet(st.town_code)
    selectStation(uid)
  }

  const showSuggestions = messages.filter((m) => m.role === 'user').length === 0
  // 空白頁範例：依畫面選取的區／站套模板
  const townName = townCode ? (stations?.find((s) => s.town_code === townCode)?.town ?? null) : null
  const openStationName = selectedUid
    ? (stations?.find((s) => s.uid === selectedUid)?.name ?? null)
    : null
  const emptySuggestions = suggestionsFor(townName, openStationName)
  // 後續建議問題僅顯示於最後一則助理訊息下方；往上捲動歷史時不顯示
  const lastMsgId = messages[messages.length - 1]?.id

  if (!open) {
    return (
      <button
        ref={fabRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-label="開啟調度助理（快捷鍵 A）"
        title="調度助理（A）"
        className="fixed bottom-[4.5rem] right-5 z-40 flex size-12 items-center justify-center rounded-full border border-control bg-float text-hot shadow-[0_8px_24px_rgba(0,0,0,.32)] hover:bg-raise active:bg-hair focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-bg)]"
      >
        <Bot className="size-[19px]" aria-hidden />
      </button>
    )
  }

  return (
    <div
      role="dialog"
      aria-labelledby="assistant-title"
      className="anim-panel drawer-float fixed bottom-[4.5rem] right-5 z-40 flex w-[min(440px,calc(100vw-2rem))] flex-col overflow-hidden rounded-sm border border-edge bg-float"
      style={{ height: 'min(560px, calc(100dvh - 11rem))' }}
    >
      <div className="phead flex-none">
        <Bot className="size-[16px] flex-none text-hot" aria-hidden />
        <h2 id="assistant-title" className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]">
          調度助理
        </h2>
        <button
          ref={newThreadBtnRef}
          type="button"
          onClick={requestNewThread}
          aria-label="開始新對話"
          className="ml-auto flex items-center gap-1 rounded-xs px-1.5 py-1 text-[0.7rem] tracking-[0.08em] text-ink3 hover:bg-hair hover:text-ink"
        >
          <MessageSquarePlus className="size-3.5" aria-hidden />
          新對話
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="關閉調度助理"
          className="-mr-1 flex items-center gap-1 rounded-xs px-1.5 py-1 text-[0.7rem] tracking-[0.08em] text-ink3 hover:bg-hair hover:text-ink"
        >
          <X className="size-3.5" aria-hidden />
          關閉
        </button>
      </div>

      {/* 行內確認：清除對話為輕量操作，不使用全螢幕對話框。預設焦點為「取消」，Esc 亦視為取消。 */}
      {confirmClear && (
        <div
          role="alert"
          className="anim-fade flex flex-none items-center gap-2 border-b border-edge bg-hot-wash px-3 py-[9px] text-[0.76rem] leading-[1.4] text-ink2"
        >
          <span className="min-w-0 flex-1">清除這串對話並開始新的？</span>
          <button
            ref={cancelClearRef}
            type="button"
            onClick={cancelClear}
            className="min-h-[24px] flex-none rounded-xs border border-control px-[10px] text-[0.72rem] text-ink2 hover:text-ink"
          >
            取消
          </button>
          <button
            type="button"
            onClick={doClearThread}
            className="min-h-[24px] flex-none rounded-xs border border-hot bg-hot px-[10px] text-[0.72rem] font-medium text-bg hover:opacity-90 active:opacity-80"
          >
            清除
          </button>
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        {/* 對話串本身不掛 aria-live：串流會逐段更新 DOM，逐 token 播報無法聽。
            tabIndex 讓鍵盤使用者能捲動歷史；播報交給 sr-only 鏡像（收尾才唸一次）。 */}
        <div
          ref={listRef}
          onScroll={onScroll}
          tabIndex={0}
          aria-label="對話內容"
          aria-busy={sending}
          className="absolute inset-0 space-y-4 overflow-y-auto px-3 py-3"
        >
          {messages.map((m) =>
            m.role === 'user' ? (
              <div key={m.id} className="flex justify-end pl-8">
                <div className="max-w-full whitespace-pre-wrap rounded-sm rounded-br-xs border border-edge bg-raise px-3 py-2 text-[0.82rem] leading-[1.5] text-ink">
                  <span className="sr-only">你說：</span>
                  {m.content}
                  <span className="sr-only">（{msgTime(m)}）</span>
                </div>
              </div>
            ) : (
              // 助理訊息：無對話框，左上以頭像作為身分標示，內容於其右側縮排
              <div key={m.id} className="group grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-2">
                <div
                  className={cn(
                    'mt-[2px] flex size-6 items-center justify-center self-start rounded-full bg-raise text-hot',
                    m.pending && 'animate-pulse motion-reduce:animate-none',
                  )}
                  aria-hidden
                >
                  <Bot className="size-[13px]" />
                </div>

                <div className="min-w-0">
                  {m.failed ? (
                    // 錯誤：設計過的內嵌卡，沿用單站行動卡語言；重試放卡內
                    <div className="flex items-start gap-2 rounded-xs border-l-2 border-hot bg-hot-wash px-3 py-2 text-[0.8rem] leading-[1.5] text-ink2">
                      <AlertTriangle className="mt-[2px] size-[14px] flex-none text-hot" aria-hidden />
                      <div className="min-w-0">
                        <span className="sr-only">助理說：</span>
                        {m.content}
                        <button
                          type="button"
                          onClick={() => retry(m.id)}
                          className="mt-1.5 flex min-h-[24px] items-center gap-1 text-[0.75rem] font-medium text-hot hover:text-ink"
                        >
                          <RefreshCw className="size-3" aria-hidden />
                          重試
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <p className="m-0 whitespace-pre-wrap text-[0.82rem] leading-[1.6] text-ink">
                        <span className="sr-only">助理說：</span>
                        {m.content}
                        {m.pending && !m.content && <span className="text-ink3">查詢中…</span>}
                        {m.pending && m.content && (
                          <span className="ml-0.5 animate-pulse text-ink3 motion-reduce:animate-none">▍</span>
                        )}
                      </p>

                      {!!m.list?.items.length && (
                        <div className="mt-2 overflow-hidden rounded-xs border border-hair">
                          <div className="bg-raise px-2.5 py-1 text-[0.66rem] font-semibold tracking-[0.08em] text-ink3">
                            {m.list.title}
                            <span className="ml-1 text-ink3/70">· {m.list.items.length}</span>
                          </div>
                          <ul className="max-h-[240px] divide-y divide-hair overflow-y-auto">
                            {m.list.items.map((it, i) => {
                              const inner = (
                                <>
                                  <span className="min-w-0 flex-1 truncate text-ink">{it.name}</span>
                                  {it.meta && (
                                    <span className="flex-none tabular-nums text-[0.72rem] text-ink3">
                                      {it.meta}
                                    </span>
                                  )}
                                </>
                              )
                              return (
                                <li key={i}>
                                  {it.uid ? (
                                    <button
                                      type="button"
                                      onClick={() => openStation(it.uid!)}
                                      className="flex w-full items-center gap-2 px-2.5 py-[7px] text-left text-[0.78rem] hover:bg-hair"
                                    >
                                      {inner}
                                    </button>
                                  ) : (
                                    <div className="flex items-center gap-2 px-2.5 py-[7px] text-[0.78rem]">
                                      {inner}
                                    </div>
                                  )}
                                </li>
                              )
                            })}
                          </ul>
                        </div>
                      )}

                      {!!m.table?.rows.length && (
                        <div className="mt-2 overflow-hidden rounded-xs border border-hair">
                          <div className="bg-raise px-2.5 py-1 text-[0.66rem] font-semibold tracking-[0.08em] text-ink3">
                            {m.table.title}
                          </div>
                          <div className="overflow-x-auto">
                            <table className="w-full border-collapse text-[0.74rem]">
                              <thead>
                                <tr className="text-ink3">
                                  <th className="px-2 py-1 text-left font-medium" aria-label="行政區" />
                                  {m.table.columns.map((c) => (
                                    <th
                                      key={c.key}
                                      className={cn(
                                        'whitespace-nowrap px-2 py-1 font-medium',
                                        c.align === 'left' ? 'text-left' : 'text-right',
                                      )}
                                    >
                                      {c.label}
                                    </th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-hair">
                                {m.table.rows.map((r, i) => (
                                  <tr key={i} className={r.town ? 'hover:bg-hair' : undefined}>
                                    <th
                                      scope="row"
                                      className="whitespace-nowrap px-2 py-1 text-left font-medium text-ink"
                                    >
                                      {r.town ? (
                                        <button
                                          type="button"
                                          onClick={() => selectTown(r.town!)}
                                          className="hover:underline"
                                        >
                                          {r.name}
                                        </button>
                                      ) : (
                                        r.name
                                      )}
                                    </th>
                                    {r.cells.map((cell, j) => (
                                      <td
                                        key={j}
                                        className={cn(
                                          'whitespace-nowrap px-2 py-1 text-ink2',
                                          m.table!.columns[j]?.align === 'left'
                                            ? 'text-left'
                                            : 'text-right tabular-nums',
                                        )}
                                      >
                                        {cell}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}

                      {!!m.actions?.length && (
                        <div className="mt-2 flex flex-wrap gap-[6px]">
                          {m.actions.map((a, i) => (
                            <button
                              key={i}
                              type="button"
                              onClick={() =>
                                a.type === 'select_station' ? openStation(a.value) : selectTown(a.value)
                              }
                              className={cn(
                                segChip(false),
                                'inline-flex min-h-[26px] items-center px-[9px] text-[0.72rem]',
                              )}
                            >
                              {a.label}
                            </button>
                          ))}
                        </div>
                      )}

                      {/* 追問 chip：只在最後一則助理訊息、且已收尾時顯示，引導問下一題（mock / live 共用 m.suggestions） */}
                      {m.id === lastMsgId && !m.pending && !m.failed && !!m.suggestions?.length && (
                        <div className="mt-2 flex flex-col items-start gap-[6px]">
                          {m.suggestions.map((s) => (
                            <button
                              key={s}
                              type="button"
                              onClick={() => send(s)}
                              className={cn(
                                segChip(false),
                                'min-h-[26px] px-[10px] py-[4px] text-left text-[0.72rem] text-ink2',
                              )}
                            >
                              {s}
                            </button>
                          ))}
                        </div>
                      )}

                      {!!m.sources?.length && (
                        <details className="mt-2 text-[0.7rem] text-ink3">
                          <summary className="cursor-pointer select-none tracking-[0.06em]">
                            參考資料（{m.sources.length}）
                          </summary>
                          <ul className="mt-1 space-y-1 pl-4">
                            {m.sources.map((s, i) => (
                              <li key={i}>
                                {s.uri ? (
                                  <a
                                    href={s.uri}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="underline decoration-hair underline-offset-2 hover:text-ink"
                                  >
                                    {s.title}
                                  </a>
                                ) : (
                                  s.title
                                )}
                                {s.snippet && <span className="text-ink3">：{s.snippet}</span>}
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}

                      {/* 換了新批次資料才顯示「以最新資料重問」（比批次時刻，不比每秒在跳的虛擬時鐘） */}
                      {!m.pending &&
                        m.query &&
                        m.dataAt &&
                        curSlot &&
                        m.dataAt !== curSlot && (
                          <button
                            type="button"
                            onClick={() => send(m.query!)}
                            className="mt-1.5 inline-flex min-h-[24px] items-center gap-1 rounded-xs text-[0.68rem] text-ink3 hover:text-ink"
                          >
                            <RefreshCw className="size-3" aria-hidden />
                            資料已更新，以最新資料重問
                          </button>
                        )}

                      {/* 複製 / 時間：常駐但低調（hover 再提亮） */}
                      {!m.pending && m.id !== 'greeting' && (
                        <div className="mt-1.5 flex items-center gap-x-3 text-[0.66rem] text-ink3 opacity-70 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                          <button
                            type="button"
                            onClick={() => copy(m)}
                            className="inline-flex min-h-[24px] items-center gap-1 rounded-xs hover:text-ink"
                          >
                            {copiedId === m.id ? (
                              <>
                                <Check className="size-3" aria-hidden />
                                已複製
                              </>
                            ) : (
                              <>
                                <Copy className="size-3" aria-hidden />
                                複製
                              </>
                            )}
                          </button>
                          <span aria-hidden className="tabular-nums">
                            {msgTime(m)}
                          </span>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            ),
          )}

          {showSuggestions && (
            <div className="flex flex-col items-start gap-[6px] pl-8 pt-1">
              {emptySuggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => send(s)}
                  className={cn(segChip(false), 'min-h-[28px] px-[10px] py-[5px] text-left text-[0.76rem]')}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>

        {!atBottom && (
          <button
            type="button"
            onClick={jumpToLatest}
            className="absolute bottom-2 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full border border-control bg-float px-[10px] py-[5px] text-[0.7rem] text-ink2 shadow-[0_4px_14px_rgba(0,0,0,.3)] hover:text-ink"
          >
            <ArrowDown className="size-3" aria-hidden />
            跳到最新
          </button>
        )}
      </div>

      <form
        className="flex flex-none items-end gap-2 border-t border-hair px-3 py-[10px]"
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
      >
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // 組字中的 Enter 只是確認 IME 選字，不送出（否則會誤送 + compositionend 把字補回輸入框）
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              send(input)
            }
          }}
          rows={1}
          aria-label="輸入問題"
          placeholder="問調度助理…"
          className="max-h-[96px] min-h-[38px] flex-1 resize-none rounded-xs border border-control bg-panel px-[10px] py-2 text-[0.82rem] leading-[1.5] text-ink outline-none placeholder:text-ink3 focus-visible:border-ink2"
        />
        {sending ? (
          <button
            type="button"
            onClick={() => abortRef.current?.abort()}
            aria-label="停止產生回應"
            className="flex size-[38px] flex-none items-center justify-center rounded-xs border border-control text-ink2 hover:border-hot hover:text-hot"
          >
            <Square className="size-[13px] fill-current" aria-hidden />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!input.trim()}
            aria-label="送出"
            className="flex size-[38px] flex-none items-center justify-center rounded-xs border border-control text-ink2 hover:text-ink active:bg-hair disabled:opacity-40"
          >
            <CornerDownLeft className="size-[15px]" aria-hidden />
          </button>
        )}
      </form>

      {/* 收尾才播報：串流過程靜默，避免逐 token 唸 */}
      <div aria-live="polite" className="sr-only">
        {announce}
      </div>
    </div>
  )
}
