/* 登入驗證 —— 前端與後端共用的型別契約。
   後端接口（先預留，隊友照這份實作，詳見 /登入驗證-後端接口規格.md）：

     POST /api/v1/auth/login    req { account, password }
                                200 { token, user }  /  401 { error:{ code, message } }
     POST /api/v1/auth/logout   Authorization: Bearer <token> → 204
     GET  /api/v1/auth/me       Authorization: Bearer <token> → 200 { user } / 401   （可選，用於重整時驗證）

   之後每支 API 都帶 Authorization: Bearer <token>；任一支回 401 → 前端清 session 導回 /login。 */

export interface AuthUser {
  /** 登入帳號（AD / 員編）—— 稽核用，前端也顯示在使用者選單 */
  account: string
  /** 顯示姓名 */
  name: string
  /** 角色（調度員 / 主管 / 檢視者…）—— 之後對應權限 */
  role: string
}

export interface LoginRequest {
  account: string
  password: string
}

export interface LoginResponse {
  /** 存取權杖（opaque 或 JWT 皆可，前端只當字串保存並回帶） */
  token: string
  user: AuthUser
}

/** 登入失敗（401）等錯誤回應體，與其他 API 一致：{ error: { code, message } } */
export interface AuthErrorBody {
  error: { code: string; message: string }
}
