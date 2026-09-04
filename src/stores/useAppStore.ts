import { create } from 'zustand'

/* 全域檢視狀態。用 slice 組合，之後加「一日回放」「路線規劃」各自一個 slice，
   不會變成一個巨檔。目前只有 selection slice。 */

interface SelectionSlice {
  /** 空字串＝全部行政區 */
  townCode: string
  /** 目前選取的站點 uid（地圖 / 警示 / 下拉都寫這裡） */
  selectedUid: string | null
  /** 每次 +1 觸發地圖把鏡頭框回目前選取的預設範圍 */
  frameNonce: number
  selectStation: (uid: string | null) => void
  /** 換區會一併清掉選取的站，並重新框鏡頭；點目前地區則只重新框 */
  selectTown: (code: string) => void
  /** 縮放/拖曳後把鏡頭框回目前選取的預設範圍 */
  refocus: () => void
}

export const useAppStore = create<SelectionSlice>((set, get) => ({
  townCode: '',
  selectedUid: null,
  frameNonce: 0,
  selectStation: (uid) => set({ selectedUid: uid }),
  selectTown: (code) => {
    const same = code === get().townCode
    set((s) => ({
      townCode: code,
      selectedUid: same ? s.selectedUid : null,
      frameNonce: s.frameNonce + 1,
    }))
  },
  refocus: () => set((s) => ({ frameNonce: s.frameNonce + 1 })),
}))
