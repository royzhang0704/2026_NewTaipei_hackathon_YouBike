import { create } from 'zustand'

/* 全域檢視狀態。用 slice 組合，之後加「一日回放」「路線規劃」各自一個 slice，
   不會變成一個巨檔。 */

export type Theme = 'light' | 'dark'
export type FontScale = 'sm' | 'md' | 'lg'
export type AlertSide = 'all' | 'shortage' | 'full'
export type AlertLevel = 'all' | 'high' | 'mid'

/** 文字大小 → root font-size 的縮放比（必須跟 index.css 的 [data-font] 對齊）。
 *  小只降 4%（本來標籤就偏小），大給 +25% 才對低視力 / 老花有意義。 */
export const FONT_PCT: Record<FontScale, number> = { sm: 0.9, md: 1, lg: 1.25 }

interface ThemeSlice {
  theme: Theme
  toggleTheme: () => void
  fontScale: FontScale
  setFontScale: (s: FontScale) => void
}

const readInitialTheme = (): Theme =>
  document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'

const readInitialFont = (): FontScale => {
  const f = document.documentElement.dataset.font
  return f === 'sm' || f === 'lg' ? f : 'md'
}

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
  /** 只改地區（讓遮罩 / chip / 警示清單跟著選取的站一致），不重新框鏡頭、不清選取。
      供「跨區選取站點」使用；地圖僅由選站的 flyTo 移動，避免鏡頭大幅拉遠再拉近。 */
  setTownQuiet: (code: string) => void
  /** 縮放/拖曳後把鏡頭框回目前選取的預設範圍 */
  refocus: () => void
  /** 行動卡「查看」指向的那筆調度單 —— 地圖把該線加粗、其餘淡化。null = 不高亮。 */
  highlightOrderId: number | null
  highlightOrder: (id: number | null) => void
}

/* 主動警示清單的篩選 —— 提到 store 讓上方 KPI 也能寫（點 KPI = 套用該篩選）。
   AlertList 讀它渲染 chip 狀態與清單；KPI 讀它決定 aria-pressed / 高亮。 */
interface AlertFilterSlice {
  alertSide: AlertSide
  alertLevel: AlertLevel
  /** 傳 partial：只帶到的欄位會變，沒帶的維持 */
  setAlertFilter: (f: Partial<{ side: AlertSide; level: AlertLevel }>) => void
}

export const useAppStore = create<SelectionSlice & ThemeSlice & AlertFilterSlice>((set, get) => ({
  theme: readInitialTheme(),
  toggleTheme: () => {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.theme = next
    try {
      localStorage.setItem('yb_theme', next)
    } catch {
      /* 無痛降級 */
    }
    set({ theme: next })
  },

  fontScale: readInitialFont(),
  setFontScale: (s) => {
    const el = document.documentElement
    if (s === 'md') delete el.dataset.font
    else el.dataset.font = s
    try {
      localStorage.setItem('yb_font', s)
    } catch {
      /* 無痛降級 */
    }
    set({ fontScale: s })
  },

  townCode: '',
  selectedUid: null,
  frameNonce: 0,
  selectStation: (uid) => set({ selectedUid: uid }),
  highlightOrderId: null,
  highlightOrder: (id) => set({ highlightOrderId: id }),
  selectTown: (code) => {
    const same = code === get().townCode
    set((s) => ({
      townCode: code,
      selectedUid: same ? s.selectedUid : null,
      frameNonce: s.frameNonce + 1,
    }))
  },
  setTownQuiet: (code) => {
    if (code !== get().townCode) set({ townCode: code })
  },
  refocus: () => set((s) => ({ frameNonce: s.frameNonce + 1 })),

  alertSide: 'all',
  alertLevel: 'all',
  setAlertFilter: (f) =>
    set((s) => ({
      alertSide: f.side ?? s.alertSide,
      alertLevel: f.level ?? s.alertLevel,
    })),
}))
