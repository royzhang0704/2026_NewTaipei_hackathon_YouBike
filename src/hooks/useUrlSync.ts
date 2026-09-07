import { useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAppStore } from '@/stores/useAppStore'

/* 把「選取的行政區 / 站點」同步進 URL query：
   - 重新整理 → 回到同一個視野
   - 複製連結 → 傳同事「看這站」
   單向：載入時 URL→store 讀一次；之後 store→URL（replace，不塞 history）。 */
export function useUrlSync() {
  const [params, setParams] = useSearchParams()
  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectTown = useAppStore((s) => s.selectTown)
  const selectStation = useAppStore((s) => s.selectStation)
  const inited = useRef(false)

  // 一次性：URL → store。selectTown 會清掉 selectedUid + bump frameNonce，所以 station 放它之後
  useEffect(() => {
    if (inited.current) return
    inited.current = true
    const town = params.get('town')
    const station = params.get('station')
    if (town) selectTown(town)
    if (station) selectStation(station)
  }, [params, selectTown, selectStation])

  // store → URL
  useEffect(() => {
    if (!inited.current) return
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (townCode) next.set('town', townCode)
        else next.delete('town')
        if (selectedUid) next.set('station', selectedUid)
        else next.delete('station')
        return next
      },
      { replace: true },
    )
  }, [townCode, selectedUid, setParams])
}
