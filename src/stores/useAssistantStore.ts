import { create } from 'zustand'

/* 調度助理僅需全域共享開關狀態；訊息串保存在 Widget 的 local state
   （元件常駐，關閉僅隱藏，對話不會清除）。 */

/** 行動卡按下「找車來補 / 找站調出」時放進來的請求；Widget 取走後清空。
    ★ 這是 StationDetail → AssistantWidget 的唯一觸發路徑：兩者不是父子，
      而助理的訊息串是 Widget 的 local state，只能由 Widget 自己送出。 */
export interface DispatchRequest {
  anchorUid: string
  anchorName: string
  action: 'refill' | 'remove'
}

interface AssistantSlice {
  open: boolean
  setOpen: (o: boolean) => void
  toggle: () => void
  pendingDispatch: DispatchRequest | null
  askDispatch: (r: DispatchRequest) => void
  clearDispatch: () => void
}

export const useAssistantStore = create<AssistantSlice>((set) => ({
  open: false,
  setOpen: (o) => set({ open: o }),
  toggle: () => set((s) => ({ open: !s.open })),
  pendingDispatch: null,
  // 一併把助理打開 —— 按鈕的意圖就是「我要看調度來源」，還要再點一次展開是多餘的
  askDispatch: (r) => set({ pendingDispatch: r, open: true }),
  clearDispatch: () => set({ pendingDispatch: null }),
}))
