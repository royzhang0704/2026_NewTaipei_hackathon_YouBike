/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 分網域部署時的後端網址；同源部署留空即可（走相對路徑）。 */
  readonly VITE_API_BASE?: string
  /** AI 調度助理：'mock'（前端規則式，預設）| 'live'（打後端 RAG endpoint）。 */
  readonly VITE_ASSISTANT_MODE?: 'mock' | 'live'
  /** live 模式的 chat endpoint；留空＝ <VITE_API_BASE>/api/v1/assistant/chat。 */
  readonly VITE_ASSISTANT_URL?: string
}
