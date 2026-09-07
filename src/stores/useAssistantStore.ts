import { create } from 'zustand'

/* 調度助理只需全域共享「開關」——訊息串留在 Widget 的 local state（元件常駐，
   關閉只是隱藏，對話不會消失）。之後若要從別處（單站頁「問助理」按鈕、快捷鍵）
   開啟並帶預填問題，加 pendingPrompt 即可。 */

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
