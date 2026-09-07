/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 分網域部署時的後端網址；同源部署留空即可（走相對路徑）。 */
  readonly VITE_API_BASE?: string
}
