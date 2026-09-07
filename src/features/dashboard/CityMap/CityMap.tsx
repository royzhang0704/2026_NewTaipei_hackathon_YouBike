import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import MapGL, {
  Layer,
  NavigationControl,
  Popup,
  Source,
  type MapLayerMouseEvent,
  type MapRef,
} from 'react-map-gl/maplibre'
import type { FeatureCollection } from 'geojson'
import { Maximize } from 'lucide-react'
import { useAlerts, useStations, useTowns } from '@/api/queries'
import { FONT_PCT, useAppStore } from '@/stores/useAppStore'
import { cn } from '@/lib/utils'
import type { AlertItem } from '@/api/types'
import districtsGeo from '@/assets/newtaipei-districts.json'
import outlineGeo from '@/assets/newtaipei-outline.json'
import {
  bgLayer,
  DISTRICT_LAYERS,
  HAS_BASEMAP,
  INTERACTIVE_LAYER_IDS,
  MAP_STYLE,
  rasterLayer,
  STATION_LAYERS,
} from './layers'
import { buildMask } from './mask'
import { hasCoord, useDistrictFocus } from './useDistrictFocus'

/** 單站抽屜的實際渲染寬（DashboardPage: clamp(360px, 25rem, 460px)，rem 隨字級縮放）。 */
const drawerWidth = (fs: number) => Math.min(460, Math.max(360, Math.round(400 * fs)))

interface HoverInfo {
  lng: number
  lat: number
  name: string
  town: string
  level: string
  side: string
  bikes: number
  action: string
}

/** 高度由外層容器決定（桌機 flex 填滿、手機給固定高）。 */
export function CityMap() {
  const mapRef = useRef<MapRef>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [ready, setReady] = useState(false)
  const [hover, setHover] = useState<HoverInfo | null>(null)
  const hoverClear = useRef<ReturnType<typeof setTimeout>>(undefined)

  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const frameNonce = useAppStore((s) => s.frameNonce)
  const refocus = useAppStore((s) => s.refocus)
  const selectStation = useAppStore((s) => s.selectStation)
  const theme = useAppStore((s) => s.theme)
  const fontScale = useAppStore((s) => s.fontScale)

  const { data: stations, isPending: stPending, isError: stError } = useStations()
  const { data: towns } = useTowns()
  // 取全市資料（不隨 townCode 縮小）：圖層依行政區自行過濾，使跨區選取的站點亦有正確風險樣式，且不產生樣式閃爍
  const { data: alerts } = useAlerts({ limit: 1000 })

  const townName = useMemo(
    () => towns?.find((t) => t.town_code === townCode)?.town ?? '',
    [towns, townCode],
  )

  const geojson = useMemo<FeatureCollection>(() => {
    const aMap = new Map<string, AlertItem>()
    for (const it of alerts?.items ?? []) aMap.set(it.station_uid, it)
    return {
      type: 'FeatureCollection',
      features: (stations ?? []).filter(hasCoord).map((s) => {
        const a = aMap.get(s.uid)
        return {
          type: 'Feature' as const,
          geometry: { type: 'Point' as const, coordinates: [s.lon, s.lat] },
          properties: {
            uid: s.uid,
            name: s.name,
            town: s.town,
            level: a?.level ?? 'none',
            side: a?.side ?? '',
            bikes: a?.dispatch?.bikes ?? 0,
            action: a?.dispatch?.action ?? '',
          },
        }
      }),
    }
  }, [stations, alerts])

  const mask = useMemo(() => buildMask(townName), [townName])

  useDistrictFocus(mapRef, ready, townName, stations, frameNonce)

  // 容器尺寸一變就 resize()：flex 高度鏈落定、中文字型載入、重整帶抽屜、breakpoint、切字級
  // 上述任一情況造成的容器尺寸變化，react-map-gl 內建偵測與 dev 模式 reuseMaps 偶有時序遺漏，此處作為後備。
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(() => mapRef.current?.getMap()?.resize())
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // 切字級 → rem 型版面 reflow → 地圖容器尺寸變。等 reflow 落定後：
  //  · 選了站 → 重新把該站擺回「可視區（扣掉抽屜）中央」，跟選站當下的行為一致。
  //  · 沒選站 → refocus() 用新容器重跑框景，跟直接點地區 chip 一致。
  const firstFont = useRef(true)
  useEffect(() => {
    if (!ready) return
    if (firstFont.current) {
      firstFont.current = false
      return
    }
    const id = requestAnimationFrame(() => {
      const m = mapRef.current?.getMap()
      if (!m) return
      m.resize()
      const uid = useAppStore.getState().selectedUid
      const st = uid ? stations?.find((s) => s.uid === uid) : null
      if (st && hasCoord(st)) {
        const drawer = window.matchMedia('(min-width: 1280px)').matches ? drawerWidth(FONT_PCT[fontScale]) : 0
        m.flyTo({
          center: [st.lon, st.lat],
          zoom: m.getZoom(),
          padding: { top: 0, bottom: 0, left: 0, right: drawer },
          duration: 0,
          bearing: 0,
          pitch: 0,
        })
      } else {
        refocus()
      }
    })
    return () => cancelAnimationFrame(id)
  }, [fontScale, ready, refocus, stations])

  // 高風險站雷達 ping：每幀更新 st-pulse。z<10.5（全市視野）透明度歸 0；
  // 縮放到單區尺度（點 chip 或手動放大都算）才動。振幅隨 zoom 增強、有下限。
  useEffect(() => {
    if (!ready) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    let raf = 0
    const tick = (t: number) => {
      const map = mapRef.current?.getMap()
      if (map?.getLayer('st-pulse')) {
        const z = map.getZoom()
        if (z < 10.5) {
          map.setPaintProperty('st-pulse', 'circle-opacity', 0)
        } else {
          const k = (t % 1600) / 1600
          const s = Math.max(0.5, Math.min(1, (z - 10.5) / 3))
          map.setPaintProperty('st-pulse', 'circle-radius', 5 + k * (14 + s * 24))
          map.setPaintProperty('st-pulse', 'circle-opacity', 0.42 * Math.sin(Math.PI * k) * s)
        }
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [ready])

  // 選中站的環：選站當下做一次「target lock」—— 半徑 30→14、描邊透明度 0.3→1（easeOutCubic ~360ms）
  useEffect(() => {
    if (!ready || !selectedUid) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const map = mapRef.current?.getMap()
    if (!map?.getLayer('st-selected')) return
    const DUR = 360
    const t0 = performance.now()
    let raf = 0
    const tick = (t: number) => {
      if (!map.getLayer('st-selected')) return
      const k = Math.min(1, (t - t0) / DUR)
      const e = 1 - (1 - k) ** 3
      map.setPaintProperty('st-selected', 'circle-radius', 14 + (1 - e) * 16)
      map.setPaintProperty('st-selected', 'circle-stroke-opacity', 0.3 + e * 0.7)
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(raf)
      if (map.getLayer('st-selected')) {
        map.setPaintProperty('st-selected', 'circle-radius', 14)
        map.setPaintProperty('st-selected', 'circle-stroke-opacity', 1)
      }
    }
  }, [selectedUid, ready])

  const onClick = useCallback(
    (e: MapLayerMouseEvent) => {
      const uid = e.features?.[0]?.properties?.uid as string | undefined
      selectStation(uid ?? null)
    },
    [selectStation],
  )

  // 移過密集點時 feature 會反覆變 null → popup 閃。離開時延遲 90ms 才收，中途有點回來就取消。
  const onMouseMove = useCallback((e: MapLayerMouseEvent) => {
    const p = e.features?.[0]?.properties
    if (!p) {
      if (!hoverClear.current) hoverClear.current = setTimeout(() => {
        setHover(null)
        hoverClear.current = undefined
      }, 90)
      return
    }
    clearTimeout(hoverClear.current)
    hoverClear.current = undefined
    setHover({
      lng: e.lngLat.lng,
      lat: e.lngLat.lat,
      name: p.name,
      town: p.town,
      level: p.level,
      side: p.side,
      bikes: p.bikes,
      action: p.action,
    })
  }, [])

  // 選了站 → 飛到「沒被單站抽屜蓋住的可視區中央」；抽屜關了 → 清掉 padding。
  // padding.right = 抽屜寬（xl 才有）。一律置中（不只 z<12 / 出界才飛），否則站會卡在
  // 邊緣或被抽屜蓋住。縮放夾在 13–16：太遠拉近、鑽太深收回一點留 context、已在區間內不動
  // —— 每次選站落在可預期的尺度，相機移動最小（不論同區 / 跨區、地圖現在多大都一致）。
  // 冷載入帶 ?station= → 第一次就位不動畫；prefers-reduced-motion → 一律就位。
  const selFirst = useRef(true)
  const selPrevNonce = useRef(frameNonce)
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!ready || !map) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    // 換區（selectTown）會一併清掉選取的站並 bump frameNonce → useDistrictFocus 正在重框該區。
    // 這時別在下面對相機下 easeTo，否則會打斷那個 fitBounds、地圖停在舊位置。
    const nonceBumped = frameNonce !== selPrevNonce.current
    selPrevNonce.current = frameNonce
    if (!selectedUid) {
      const first = selFirst.current
      selFirst.current = false
      // 換區造成的取消選取 → useDistrictFocus 正在重框（它會先清掉抽屜殘留的 transform padding
      // 再算 fitBounds）→ 這裡不要再對相機下 easeTo，否則會打斷那個 fitBounds。
      if (nonceBumped) return
      // 僅關閉抽屜：padding 由 400 回到 0，以動畫過渡（setPadding 為瞬間套用，會造成跳動）
      map.easeTo({ padding: { top: 0, bottom: 0, left: 0, right: 0 }, duration: first || reduce ? 0 : 400 })
      return
    }
    const st = stations?.find((s) => s.uid === selectedUid)
    if (!st || !hasCoord(st)) return // 站表還沒到 → 先不動，也不消耗 selFirst
    const drawer = window.matchMedia('(min-width: 1280px)').matches ? drawerWidth(FONT_PCT[fontScale]) : 0
    const z = map.getZoom()
    const first = selFirst.current
    selFirst.current = false
    map.flyTo({
      center: [st.lon, st.lat],
      zoom: Math.min(16, Math.max(13, z)),
      padding: { top: 0, bottom: 0, left: 0, right: drawer },
      duration: first || reduce ? 0 : 500,
      bearing: 0,
      pitch: 0,
    })
    // frameNonce / fontScale 刻意不進 deps：兩者都是「讀當下值」而非要靠它們重跑
    //   · frameNonce → 只拿來判斷「這次取消選取是不是換區造成的」
    //   · fontScale 變動由上面的 firstFont effect 專責重新擺位
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedUid, ready, stations])

  return (
    <div ref={wrapRef} className="relative h-full w-full">
      <MapGL
        ref={mapRef}
        reuseMaps
        mapStyle={MAP_STYLE}
        initialViewState={{ longitude: 121.46, latitude: 25.01, zoom: 10.4 }}
        minZoom={8}
        maxZoom={18}
        maxBounds={[121.0, 24.45, 122.35, 25.5]}
        // 北向朝上，不給旋轉/傾斜（調度地圖不需要）
        dragRotate={false}
        pitchWithRotate={false}
        touchPitch={false}
        interactiveLayerIds={INTERACTIVE_LAYER_IDS}
        onLoad={(e) => {
          const m = e.target
          m.touchZoomRotate.disableRotation()
          m.keyboard.disableRotation()
          // ★ 等容器尺寸真的定型才 setReady → 框景（useDistrictFocus / flyTo）對得上實際大小。
          //   落定＝影響版面的字型就位（phead / chip 列 reflow）＋ 兩個 rAF ＋ resize。
          //   內文 CJK 走系統字（PingFang / 系統預設），即時可用；Noto 下載完只影響少數
          //   serif 標題，不再增加地圖欄高度，故等待上限縮短為 350ms，冷快取時亦不致長時間等待。
          const settle = () => {
            m.resize()
            requestAnimationFrame(() =>
              requestAnimationFrame(() => {
                m.resize()
                setReady(true)
              }),
            )
          }
          const fonts = document.fonts
          if (fonts && fonts.status !== 'loaded') {
            let done = false
            const go = () => {
              if (done) return
              done = true
              settle()
            }
            fonts.ready.then(go, go)
            setTimeout(go, 350)
          } else settle()
        }}
        onError={(e) => console.error('[CityMap]', e.error?.message ?? e)}
        onClick={onClick}
        onMouseMove={onMouseMove}
        onMouseLeave={() => setHover(null)}
        cursor={hover ? 'pointer' : 'default'}
        attributionControl={{ compact: true }}
        style={{ width: '100%', height: '100%' }}
      >
        <NavigationControl position="top-right" showCompass={false} />

        <Layer {...bgLayer(theme)} />
        {HAS_BASEMAP && <Layer {...rasterLayer(theme)} />}

        {/* 選區聚光燈：選中區以外變暗（深色蓋深、淺色往頁底色洗淡） */}
        <Source id="mask" type="geojson" data={mask}>
          <Layer
            id="district-mask"
            type="fill"
            paint={
              {
                'fill-color': theme === 'light' ? '#f4f3ef' : '#0b0d10',
                'fill-opacity': theme === 'light' ? 0.66 : 0.45,
                'fill-opacity-transition': { duration: 200 },
              } as never
            }
          />
        </Source>

        <Source id="city" type="geojson" data={outlineGeo as unknown as FeatureCollection}>
          <Layer {...DISTRICT_LAYERS.cityOutline(townName)} />
        </Source>

        <Source id="districts" type="geojson" data={districtsGeo as unknown as FeatureCollection}>
          <Layer {...DISTRICT_LAYERS.fill(townName)} />
          <Layer {...DISTRICT_LAYERS.line(theme)} />
          <Layer {...DISTRICT_LAYERS.lineSel(townName)} />
        </Source>

        <Source id="stations" type="geojson" data={geojson}>
          <Layer {...STATION_LAYERS.glow(townName)} />
          <Layer {...STATION_LAYERS.pulse(townName)} />
          <Layer {...STATION_LAYERS.base(townName, selectedUid, theme)} />
          <Layer {...STATION_LAYERS.alert(townName, selectedUid, theme)} />
          <Layer {...STATION_LAYERS.selected(selectedUid, theme)} />
        </Source>

        {hover && (
          <Popup longitude={hover.lng} latitude={hover.lat} closeButton={false} closeOnClick={false} offset={10}>
            <b>{hover.name}</b>
            <br />
            {hover.town}
            {hover.level === 'none'
              ? '供需健康'
              : `${hover.side === 'shortage' ? '缺車' : '滿站'}・${hover.level === 'high' ? '高' : hover.level === 'mid' ? '中' : '低'}風險`}
            {hover.bikes ? (
              <>
                <br />
                {hover.action === 'refill' ? '補車' : '取車'} {hover.bikes} 台
              </>
            ) : null}
          </Popup>
        )}
      </MapGL>

      <button
        onClick={() => refocus()}
        title="回到目前地區範圍"
        aria-label="回到目前地區範圍"
        className="absolute bottom-2 left-2 z-10 flex size-[29px] items-center justify-center rounded-xs border border-edge bg-panel text-ink2 hover:text-ink"
      >
        <Maximize className="size-[15px]" />
      </button>

      {/* 圖例移到左上：右上留給 NavigationControl（原本兩者疊在一起）。pointer-events-none 不擋操作 */}
      <div className="pointer-events-none absolute left-2 top-2 z-10 rounded-xs border border-edge bg-bg/85 px-[14px] py-[10px] text-[0.72rem] leading-[1.9] tracking-[0.06em] text-ink2 backdrop-blur">
        <div className="flex items-center gap-[10px]">
          <span className="inline-block size-[11px] rounded-full bg-hot" />缺車風險
        </div>
        <div className="flex items-center gap-[10px]">
          <span className="inline-block size-[11px] rounded-full border-[2px] border-cold bg-transparent" />滿站風險
        </div>
        <div className="flex items-center gap-[10px]">
          <span className="inline-block size-[11px] rounded-full border border-ink3" />供需健康
        </div>
        <div className="mt-[6px] flex items-center gap-[10px] border-t border-hair pt-[6px] text-ink3">
          <span className="relative inline-flex size-[11px] items-center justify-center">
            <span className="absolute inline-block size-[11px] animate-ping rounded-full bg-hot/60" />
            <span className="inline-block size-[6px] rounded-full bg-hot" />
          </span>
          高風險
        </div>
        <div className="flex items-center gap-[10px] text-ink3">
          <span className="inline-block size-[11px] rounded-full border-2 border-ink" />
          已選取
        </div>
      </div>

      {/* 冷載入：地圖框到 URL 視野（?town= / ?station=）之前先整片蓋住，
          避免看到預設視野 → 再跳一下。框景完成（ready）後淡出。 */}
      <div
        aria-hidden={ready}
        className={cn(
          'absolute inset-0 z-30 flex items-center justify-center bg-panel transition-opacity duration-300',
          ready ? 'pointer-events-none opacity-0' : 'opacity-100',
        )}
      >
        <span className="flex items-center gap-2 text-[0.78rem] text-ink3">
          <span
            className="size-3 animate-spin rounded-full border border-current border-t-transparent motion-reduce:animate-none"
            aria-hidden
          />
          載入地圖…
        </span>
      </div>

      {/* 站點資料未載入或失敗：底圖仍顯示，中央放置狀態標籤，不完全遮蔽 */}
      {(stError || (stPending && !stations)) && (
        <div className="anim-fade pointer-events-none absolute inset-0 z-20 flex items-center justify-center">
          <div
            role="status"
            className={cn(
              'rounded-xs border bg-panel/90 px-4 py-2 text-[0.78rem] backdrop-blur',
              stError ? 'border-hot text-hot' : 'border-edge text-ink2',
            )}
          >
            {stError ? '地圖資料載入失敗，請確認後端' : '載入地圖資料…'}
          </div>
        </div>
      )}
    </div>
  )
}
