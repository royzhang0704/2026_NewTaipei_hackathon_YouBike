/* 登入的傳輸層：依 VITE_AUTH_MODE 切 mock / live。
   store 只呼叫 authLogin / authLogout，不知道背後是本地假帳號還是後端 endpoint。 */

import { getApiBase } from '@/api/client'
import type { AuthUser, LoginResponse } from './types'

const MODE = import.meta.env.VITE_AUTH_MODE ?? 'mock'

export function authMode(): 'mock' | 'live' {
  return MODE === 'live' ? 'live' : 'mock'
}

function endpoint(path: string): string {
  return import.meta.env.VITE_AUTH_URL
    ? `${import.meta.env.VITE_AUTH_URL.replace(/\/+$/, '')}${path}`
    : `${getApiBase()}/api/v1/auth${path}`
}

/** 已知帳號 → 姓名 / 角色（mock 用，模擬「登入後由目錄服務補上」）。
    不在表內的帳號一律以帳號字串當名稱 —— mock 模式維持「任意帳密可進」。 */
const DEMO_ACCOUNTS: Record<string, { name: string; role: string }> = {
  'chang.zc': { name: '張志強', role: '調度員' },
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function loginMock(account: string): Promise<LoginResponse> {
  const a = account.trim()
  await sleep(280) // 讓「送出中」狀態看得到
  const known = DEMO_ACCOUNTS[a.toLowerCase()]
  const user: AuthUser = known
    ? { account: a, ...known }
    : { account: a, name: a || '調度員', role: '調度員' }
  return { token: `mock.${a}.${Date.now()}`, user }
}

async function loginLive(account: string, password: string): Promise<LoginResponse> {
  const res = await fetch(endpoint('/login'), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ account, password }),
  })
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const msg = body?.error?.message ?? (res.status === 401 ? '帳號或密碼錯誤' : `登入失敗（${res.status}）`)
    throw new Error(msg)
  }
  if (!body?.token || !body?.user) throw new Error('登入回應格式不正確')
  return body as LoginResponse
}

export async function authLogin(account: string, password: string): Promise<LoginResponse> {
  return authMode() === 'live' ? loginLive(account, password) : loginMock(account)
}

export async function authLogout(token: string | null): Promise<void> {
  if (authMode() !== 'live' || !token) return
  // 失敗不擋前端登出（本地 session 一定清掉），只盡力通知後端註銷
  try {
    await fetch(endpoint('/logout'), {
      method: 'POST',
      headers: { authorization: `Bearer ${token}` },
    })
  } catch {
    /* ignore */
  }
}
