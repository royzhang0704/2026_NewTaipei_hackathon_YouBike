/* 調度助理的傳輸層：依 VITE_ASSISTANT_MODE 切 mock / live。
   UI 只呼叫 sendChat()，不知道背後是規則式假腦還是後端 RAG。 */

import { getApiBase } from '@/api/client'
import type { AssistantContext, ChatHandlers, ChatResult, WireMessage } from './types'
import { mockChat, type MockSnapshot } from './mockBrain'

const MODE = import.meta.env.VITE_ASSISTANT_MODE ?? 'mock'

export function assistantMode(): 'mock' | 'live' {
  return MODE === 'live' ? 'live' : 'mock'
}

export async function sendChat(
  messages: WireMessage[],
  context: AssistantContext,
  snapshot: MockSnapshot,
  handlers: ChatHandlers = {},
): Promise<ChatResult> {
  if (assistantMode() === 'live') return sendLive(messages, context, handlers)
  return mockChat(messages, snapshot, handlers)
}

function endpoint(): string {
  return import.meta.env.VITE_ASSISTANT_URL || `${getApiBase()}/api/v1/assistant/chat`
}

async function sendLive(
  messages: WireMessage[],
  context: AssistantContext,
  h: ChatHandlers,
): Promise<ChatResult> {
  const res = await fetch(endpoint(), {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
    body: JSON.stringify({ messages, context }),
    signal: h.signal,
  })
  if (!res.ok) throw new Error(`assistant ${res.status}`)

  // 非串流 fallback
  if (res.headers.get('content-type')?.includes('application/json')) {
    const j = (await res.json()) as { reply?: string; sources?: ChatResult['sources']; actions?: ChatResult['actions'] }
    if (j.sources) h.onSources?.(j.sources)
    if (j.actions) h.onActions?.(j.actions)
    if (j.reply) h.onDelta?.(j.reply)
    return { content: j.reply ?? '', sources: j.sources, actions: j.actions }
  }

  if (!res.body) throw new Error('assistant: 無回應內容')
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  let content = ''
  let sources: ChatResult['sources']
  let actions: ChatResult['actions']

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += value
    const frames = buf.split('\n\n')
    buf = frames.pop() ?? ''
    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data:'))
      if (!line) continue
      let ev: { type: string; text?: string; items?: unknown; message?: string }
      try {
        ev = JSON.parse(line.slice(5).trim())
      } catch {
        continue
      }
      if (ev.type === 'delta' && ev.text) {
        content += ev.text
        h.onDelta?.(ev.text)
      } else if (ev.type === 'sources') {
        sources = ev.items as ChatResult['sources']
        if (sources) h.onSources?.(sources)
      } else if (ev.type === 'actions') {
        actions = ev.items as ChatResult['actions']
        if (actions) h.onActions?.(actions)
      } else if (ev.type === 'error') {
        throw new Error(ev.message || '助理服務錯誤')
      }
    }
  }
  return { content, sources, actions }
}
