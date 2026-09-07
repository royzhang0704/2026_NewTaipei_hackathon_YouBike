/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 分網域部署時的後端網址；同源部署留空即可（走相對路徑）。 */
  readonly VITE_API_BASE?: string
  /** 調度助理：'mock'（前端內建回應，預設）| 'live'（呼叫後端對話端點）。 */
  readonly VITE_ASSISTANT_MODE?: 'mock' | 'live'
  /** live 模式的對話端點；留空＝ <VITE_API_BASE>/api/v1/assistant/chat。 */
  readonly VITE_ASSISTANT_URL?: string
  /** 登入驗證：'mock'（前端假帳號，任意帳密可進，預設）| 'live'（打後端 /api/v1/auth）。 */
  readonly VITE_AUTH_MODE?: 'mock' | 'live'
  /** live 模式的 auth base；留空＝ <VITE_API_BASE>/api/v1/auth。 */
  readonly VITE_AUTH_URL?: string
}
