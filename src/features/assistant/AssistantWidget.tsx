import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Bot, CornerDownLeft, X } from 'lucide-react'
import { useAlerts, useHealth, useStations, useTowns } from '@/api/queries'
import { useAppStore } from '@/stores/useAppStore'
import { useAssistantStore } from '@/stores/useAssistantStore'
import { segChip } from '@/components/ui/segChip'
import { cn } from '@/lib/utils'
import { assistantMode, sendChat } from './api'
import type { ChatMessage } from './types'

const uid = () => Math.random().toString(36).slice(2, 10)

const GREETING: ChatMessage = {
  id: 'greeting',
  role: 'assistant',
  content: '嗨，我是調度助理。可以問我目前的供需與調度狀態，或點下面的範例。',
}

const SUGGESTIONS = [
  '現在有幾個高風險站？',
  '哪些站一小時內要補車？',
  '板橋區狀況如何？',
]

export function AssistantWidget() {
  const open = useAssistantStore((s) => s.open)
  const setOpen = useAssistantStore((s) => s.setOpen)

  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectStation = useAppStore((s) => s.selectStation)
  const selectTown = useAppStore((s) => s.selectTown)

  // 全市（不套目前篩選）——助理要能回答任何一區
  const { data: alerts } = useAlerts({ limit: 1000 })
  const { data: stations } = useStations()
  const { data: towns } = useTowns()
  const { data: health } = useHealth()

  const [messages, setMessages] = useState<ChatMessage[]>([GREETING])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)

  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const patchLast = useCallback((patch: (m: ChatMessage) => Partial<ChatMessage>) => {
    setMessages((ms) => {
      if (!ms.length) return ms
      const i = ms.length - 1
      return [...ms.slice(0, i), { ...ms[i], ...patch(ms[i]) }]
    })
  }, [])

  // 開啟時把焦點帶進輸入框；Esc 關閉
  useEffect(() => {
    if (!open) return
    inputRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setOpen])

  // 新訊息 / 串流 → 捲到底
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [messages])

  // 卸載時中止進行中的請求
  useEffect(() => () => abortRef.current?.abort(), [])

  const snapshot = useMemo(
    () => ({ alerts: alerts?.items ?? [], stations: stations ?? [], towns: towns ?? [], health }),
    [alerts, stations, towns, health],
  )

  async function send(text: string) {
    const q = text.trim()
    if (!q || sending) return
    setInput('')
    const history = messages
      .filter((m) => m.id !== 'greeting')
      .map((m) => ({ role: m.role, content: m.content }))
    const userMsg: ChatMessage = { id: uid(), role: 'user', content: q }
    const botMsg: ChatMessage = { id: uid(), role: 'assistant', content: '', pending: true }
    setMessages((ms) => [...ms, userMsg, botMsg])
    setSending(true)

    const ac = new AbortController()
    abortRef.current = ac
    try {
      const res = await sendChat(
        [...history, { role: 'user', content: q }],
        {
          town_code: townCode || null,
          station_uid: selectedUid,
          virtual_now: health?.virtual_now ?? health?.now ?? null,
        },
        snapshot,
        {
          signal: ac.signal,
          onDelta: (d) => patchLast((m) => ({ content: m.content + d })),
          onSources: (s) => patchLast(() => ({ sources: s })),
          onActions: (a) => patchLast(() => ({ actions: a })),
        },
      )
      patchLast((m) => ({
        content: res.content || m.content || '（無回應）',
        sources: res.sources,
        actions: res.actions,
        pending: false,
      }))
    } catch {
      if (!ac.signal.aborted) {
        patchLast(() => ({ content: '抱歉，查詢時出了點問題，請再試一次。', pending: false, failed: true }))
      }
    } finally {
      setSending(false)
      abortRef.current = null
    }
  }

  const mode = assistantMode()
  const showSuggestions = messages.filter((m) => m.role === 'user').length === 0

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="開啟調度助理"
        className="fixed bottom-5 right-5 z-40 flex items-center gap-2 rounded-full border border-control bg-float px-[14px] py-[10px] text-[0.8rem] font-medium text-ink shadow-[0_8px_24px_rgba(0,0,0,.32)] hover:bg-raise"
      >
        <Bot className="size-[17px] text-hot" aria-hidden />
        調度助理
      </button>
    )
  }

  return (
    <div
      role="dialog"
      aria-label="調度助理"
      className="anim-panel drawer-float fixed bottom-5 right-5 z-40 flex w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-sm border border-edge bg-float"
      style={{ height: 'min(560px, calc(100dvh - 7rem))' }}
    >
      <div className="phead flex-none">
        <Bot className="size-[16px] flex-none text-hot" aria-hidden />
        <h2 className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]">調度助理</h2>
        {mode === 'mock' && (
          <span className="rounded-xs border border-hair px-[5px] py-px text-[0.6rem] tracking-[0.08em] text-ink3">
            示範
          </span>
        )}
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="關閉調度助理"
          className="ml-auto -mr-1 flex items-center gap-1 rounded-xs px-1.5 py-1 text-[0.7rem] tracking-[0.08em] text-ink3 hover:bg-hair hover:text-ink"
        >
          <X className="size-3.5" aria-hidden />
          關閉
        </button>
      </div>

      <div ref={listRef} role="log" aria-live="polite" className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {messages.map((m) =>
          m.role === 'user' ? (
            <div key={m.id} className="flex justify-end">
              <p className="m-0 max-w-[82%] whitespace-pre-wrap rounded-sm rounded-br-xs bg-raise px-3 py-2 text-[0.82rem] leading-[1.5] text-ink">
                {m.content}
              </p>
            </div>
          ) : (
            <div key={m.id} className="max-w-[92%]">
              <p
                className={cn(
                  'm-0 whitespace-pre-wrap text-[0.82rem] leading-[1.6]',
                  m.failed ? 'text-hot' : 'text-ink2',
                )}
              >
                {m.content}
                {m.pending && <span className="ml-0.5 animate-pulse text-ink3">▍</span>}
              </p>

              {!!m.actions?.length && (
                <div className="mt-2 flex flex-wrap gap-[6px]">
                  {m.actions.map((a, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => {
                        if (a.type === 'select_station') selectStation(a.value)
                        else selectTown(a.value)
                      }}
                      className={cn(segChip(false), 'px-[8px] py-[3px] text-[0.72rem]')}
                    >
                      {a.label}
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
                          <a href={s.uri} target="_blank" rel="noopener noreferrer" className="underline decoration-hair underline-offset-2 hover:text-ink">
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
            </div>
          ),
        )}

        {showSuggestions && (
          <div className="flex flex-col items-start gap-[6px] pt-1">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => send(s)}
                className={cn(segChip(false), 'px-[10px] py-[5px] text-left text-[0.76rem]')}
              >
                {s}
              </button>
            ))}
          </div>
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
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send(input)
            }
          }}
          rows={1}
          aria-label="輸入問題"
          placeholder="問調度助理…"
          className="max-h-[96px] min-h-[38px] flex-1 resize-none rounded-xs border border-control bg-panel px-[10px] py-2 text-[0.82rem] leading-[1.5] text-ink outline-none placeholder:text-ink3 focus-visible:border-ink2"
        />
        <button
          type="submit"
          disabled={!input.trim() || sending}
          aria-label="送出"
          className="flex size-[38px] flex-none items-center justify-center rounded-xs border border-control text-ink2 hover:text-ink disabled:opacity-40"
        >
          <CornerDownLeft className="size-[15px]" aria-hidden />
        </button>
      </form>
    </div>
  )
}
