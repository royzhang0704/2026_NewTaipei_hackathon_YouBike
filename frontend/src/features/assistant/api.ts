/* 調度助理的傳輸層：呼叫後端 SSE 端點，把事件流轉成 ChatHandlers 回呼。
   UI 僅呼叫 sendChat()。回應由後端組（即時查 DB 的 fast-path + AgentCore Harness）。 */

import { getApiBase } from '@/api/client'
import type {
  AssistantContext,
  AssistantDispatch,
  ChatHandlers,
  ChatResult,
  WireMessage,
} from './types'

function endpoint(): string {
  return import.meta.env.VITE_ASSISTANT_URL || `${getApiBase()}/api/v1/assistant/chat`
}

export async function sendChat(
  messages: WireMessage[],
  context: AssistantContext,
  h: ChatHandlers = {},
): Promise<ChatResult> {
  const res = await fetch(endpoint(), {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
    body: JSON.stringify({ messages, context }),
    signal: h.signal,
  })
  if (!res.ok) throw new Error(`assistant ${res.status}`)

  // 非串流回應
  if (res.headers.get('content-type')?.includes('application/json')) {
    const j = (await res.json()) as {
      reply?: string
      sources?: ChatResult['sources']
      actions?: ChatResult['actions']
      list?: ChatResult['list']
      table?: ChatResult['table']
      suggestions?: ChatResult['suggestions']
      dispatch?: AssistantDispatch
    }
    if (j.sources) h.onSources?.(j.sources)
    if (j.actions) h.onActions?.(j.actions)
    if (j.list) h.onList?.(j.list)
    if (j.table) h.onTable?.(j.table)
    if (j.dispatch) h.onDispatch?.(j.dispatch)
    if (j.suggestions) h.onSuggestions?.(j.suggestions)
    if (j.reply) h.onDelta?.(j.reply)
    return {
      content: j.reply ?? '',
      sources: j.sources,
      actions: j.actions,
      list: j.list,
      table: j.table,
      suggestions: j.suggestions,
      dispatch: j.dispatch,
    }
  }

  if (!res.body) throw new Error('assistant: 無回應內容')
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  let content = ''
  let sources: ChatResult['sources']
  let actions: ChatResult['actions']
  let list: ChatResult['list']
  let table: ChatResult['table']
  let suggestions: ChatResult['suggestions']
  let dispatch: AssistantDispatch | undefined

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += value
    const frames = buf.split('\n\n')
    buf = frames.pop() ?? ''
    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data:'))
      if (!line) continue
      let ev: {
        type: string
        text?: string
        items?: unknown
        title?: string
        columns?: unknown
        rows?: unknown
        message?: string
        origin?: string
        anchor?: unknown
        shortfall?: number
      }
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
      } else if (ev.type === 'list') {
        list = { title: ev.title ?? '', items: (ev.items ?? []) as NonNullable<ChatResult['list']>['items'] }
        h.onList?.(list)
      } else if (ev.type === 'table') {
        table = {
          title: ev.title ?? '',
          columns: (ev.columns ?? []) as NonNullable<ChatResult['table']>['columns'],
          rows: (ev.rows ?? []) as NonNullable<ChatResult['table']>['rows'],
        }
        h.onTable?.(table)
      } else if (ev.type === 'dispatch') {
        // ★ 這個事件在 delta 之前到 —— 卡片先渲染，文案稍後才補上
        dispatch = {
          origin: ev.origin ?? '',
          anchor: ev.anchor as AssistantDispatch['anchor'],
          items: (ev.items ?? []) as AssistantDispatch['items'],
          shortfall: ev.shortfall ?? 0,
        }
        h.onDispatch?.(dispatch)
      } else if (ev.type === 'suggestions') {
        suggestions = ev.items as ChatResult['suggestions']
        if (suggestions) h.onSuggestions?.(suggestions)
      } else if (ev.type === 'error') {
        throw new Error(ev.message || '助理服務錯誤')
      }
    }
  }
  return { content, sources, actions, list, table, suggestions, dispatch }
}
