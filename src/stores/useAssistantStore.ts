import { create } from 'zustand'

/* 調度助理僅需全域共享開關狀態；訊息串保存在 Widget 的 local state
   （元件常駐，關閉僅隱藏，對話不會清除）。 */

interface AssistantSlice {
  open: boolean
  setOpen: (o: boolean) => void
  toggle: () => void
}

export const useAssistantStore = create<AssistantSlice>((set) => ({
  open: false,
  setOpen: (o) => set({ open: o }),
  toggle: () => set((s) => ({ open: !s.open })),
}))
