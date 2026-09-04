import type { ApiError } from './types'

/* 單一 fetch 包裝：
   - base URL：預設走相對路徑（dev 由 Vite proxy、prod 由反向代理）；
     右上角可覆寫，存 localStorage，沿用原型的 yb_api key。
   - 錯誤正規化：後端錯誤格式為 { error: { code, message } }，統一丟成 ApiError。 */

const LS_KEY = 'yb_api'

export function getApiBase(): string {
  try {
    return localStorage.getItem(LS_KEY)?.replace(/\/+$/, '') ?? ''
  } catch {
    return ''
  }
}

export function setApiBase(v: string) {
  try {
    const clean = v.trim().replace(/\/+$/, '')
    if (clean) localStorage.setItem(LS_KEY, clean)
    else localStorage.removeItem(LS_KEY)
  } catch {
    /* 無痛降級 */
  }
}

export class ApiFail extends Error implements ApiError {
  code: string
  constructor(code: string, message: string) {
    super(message)
    this.code = code
    this.name = 'ApiFail'
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(getApiBase() + path, init)
  const body = await r.json().catch(() => null)
  if (!r.ok) {
    const e = body?.error
    throw new ApiFail(e?.code ?? `HTTP_${r.status}`, e?.message ?? r.statusText)
  }
  return body as T
}
