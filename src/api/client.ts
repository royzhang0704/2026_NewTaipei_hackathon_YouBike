import type { ApiError } from './types'

/* 單一 fetch 包裝：
   - base URL：預設走相對路徑（dev 由 Vite proxy、prod 由同源反向代理 / CloudFront）。
   - 錯誤正規化：後端錯誤格式為 { error: { code, message } }，統一丟成 ApiError。 */

const LS_KEY = 'yb_api'

/** 後端 base URL 決策順序：
 *  1. VITE_API_BASE —— build 時指定，只有分網域部署才需要
 *  2. localStorage（僅開發模式）—— 在 devtools 手動 setItem 覆寫，無 UI，build 產物會剃掉這段
 *  3. ''（相對路徑）—— 同源部署 / dev proxy 的正常路徑 */
export function getApiBase(): string {
  const env = import.meta.env.VITE_API_BASE
  if (env) return env.replace(/\/+$/, '')
  if (import.meta.env.DEV) {
    try {
      const v = localStorage.getItem(LS_KEY)
      if (v) return v.replace(/\/+$/, '')
    } catch {
      /* 無痛降級 */
    }
  }
  return ''
}

export class ApiFail extends Error implements ApiError {
  code: string
  constructor(code: string, message: string) {
    super(message)
    this.code = code
    this.name = 'ApiFail'
  }
}

/** 單次請求逾時：endpoint 卡住時不要無限等（瀏覽器預設可到數分鐘）。 */
const TIMEOUT_MS = 12_000

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  // 逾時的 signal + 呼叫端傳入的（react-query 換 key / unmount 時的取消）任一觸發都中止
  const signal = init?.signal
    ? AbortSignal.any([ctrl.signal, init.signal])
    : ctrl.signal

  let r: Response
  try {
    r = await fetch(getApiBase() + path, { ...init, signal })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // 逾時 controller 有動 → 逾時；否則是呼叫端取消（react-query），直接往上丟讓它靜默
      if (ctrl.signal.aborted) throw new ApiFail('TIMEOUT', `連線逾時（超過 ${TIMEOUT_MS / 1000} 秒）`)
      throw err
    }
    throw new ApiFail('NETWORK', err instanceof Error ? err.message : '連線失敗')
  } finally {
    clearTimeout(timer)
  }

  const body = await r.json().catch(() => null)
  if (!r.ok) {
    const e = body?.error
    throw new ApiFail(e?.code ?? `HTTP_${r.status}`, e?.message ?? r.statusText)
  }
  return body as T
}
