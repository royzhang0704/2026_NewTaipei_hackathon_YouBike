import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import { useAlerts, useHealth, useStations, useTowns } from '@/api/queries'
import { useAppStore } from '@/stores/useAppStore'
import { useAssistantStore } from '@/stores/useAssistantStore'
import { mdhm } from '@/lib/format'
import { segChip } from '@/components/ui/segChip'
import { cn } from '@/lib/utils'
import { sendChat } from './api'
import type { ChatMessage } from './types'

const uid = () => Math.random().toString(36).slice(2, 10)
const STORE_KEY = 'yb_assistant_thread'
const MAX_KEEP = 50 // 持久化只留最近 N 則

const SUGGESTIONS = ['現在有幾個高風險站？', '哪些站一小時內要補車？', '板橋區狀況如何？']

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

  // 取全市資料（不套用目前篩選），使助理可回答任一行政區
  const { data: alerts } = useAlerts({ limit: 1000 })
  const { data: stations } = useStations()
  const { data: towns } = useTowns()
  const { data: health } = useHealth()

  const [messages, setMessages] = useState<ChatMessage[]>(loadThread)
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

  // 目前的資料時刻（虛擬時鐘）——回答時記在訊息上，之後比對是否過期
  const dataNow = health?.virtual_now ?? health?.now ?? null

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

  const snapshot = useMemo(
    () => ({ alerts: alerts?.items ?? [], stations: stations ?? [], towns: towns ?? [], health }),
    [alerts, stations, towns, health],
  )

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
        { town_code: townCode || null, station_uid: selectedUid, virtual_now: dataNow },
        snapshot,
        {
          signal: ac.signal,
          // 減少動態偏好：不逐字更新，等收尾一次補上整段
          onDelta: reduce ? undefined : (d) => patchMsg(botId, (m) => ({ content: m.content + d })),
          onSources: (s) => patchMsg(botId, () => ({ sources: s })),
          onActions: (a) => patchMsg(botId, () => ({ actions: a })),
          onSuggestions: (s) => patchMsg(botId, () => ({ suggestions: s })),
        },
      )
      patchMsg(botId, (m) => ({
        content: res.content || m.content || '（無回應）',
        sources: res.sources,
        actions: res.actions,
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
        patchMsg(botId, () => ({
          content: '查詢失敗，請重試。',
          pending: false,
          failed: true,
        }))
        setAnnounce('查詢失敗，請重試')
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
      { id: uid(), role: 'user', content: q, at: now },
      { id: botId, role: 'assistant', content: '', pending: true, at: now, query: q, dataAt: dataNow },
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
    patchMsg(id, () => ({ content: '', pending: true, failed: false, at: Date.now(), dataAt: dataNow }))
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
    try {
      localStorage.removeItem(STORE_KEY)
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

  const showSuggestions = messages.filter((m) => m.role === 'user').length === 0
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
      className="anim-panel drawer-float fixed bottom-[4.5rem] right-5 z-40 flex w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-sm border border-edge bg-float"
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
                  <span className="sr-only">（{clock(m.at)}）</span>
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

                      {!!m.actions?.length && (
                        <div className="mt-2 flex flex-wrap gap-[6px]">
                          {m.actions.map((a, i) => (
                            <button
                              key={i}
                              type="button"
                              onClick={() => {
                                if (a.type === 'select_station') {
                                  // 選到篩選範圍外的站 → 靜默把地區切到該站所屬區（遮罩 / chip 一致），
                                  // 不重框地圖，鏡頭只由選站的 flyTo 帶過去
                                  const st = stations?.find((s) => s.uid === a.value)
                                  if (st && townCode && st.town_code !== townCode) setTownQuiet(st.town_code)
                                  selectStation(a.value)
                                } else {
                                  selectTown(a.value)
                                }
                              }}
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

                      {/* 資料過期時常駐顯示「以最新資料重問」提示（重要資訊，不隱藏於 hover） */}
                      {!m.pending &&
                        m.query &&
                        m.dataAt &&
                        dataNow &&
                        mdhm(m.dataAt) !== mdhm(dataNow) && (
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
                            {clock(m.at)}
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
              {SUGGESTIONS.map((s) => (
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
