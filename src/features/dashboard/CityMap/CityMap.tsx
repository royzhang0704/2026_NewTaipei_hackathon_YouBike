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
import { useAppStore } from '@/stores/useAppStore'
import type { AlertItem } from '@/api/types'
import districtsGeo from '@/assets/newtaipei-districts.json'
import outlineGeo from '@/assets/newtaipei-outline.json'
import {
  DISTRICT_LAYERS,
  INTERACTIVE_LAYER_IDS,
  MAP_STYLE,
  STATION_LAYERS,
} from './layers'
import { buildMask } from './mask'
import { hasCoord, useDistrictFocus } from './useDistrictFocus'

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
  const [ready, setReady] = useState(false)
  const [hover, setHover] = useState<HoverInfo | null>(null)
  const hoverClear = useRef<ReturnType<typeof setTimeout>>(undefined)

  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const frameNonce = useAppStore((s) => s.frameNonce)
  const refocus = useAppStore((s) => s.refocus)
  const selectStation = useAppStore((s) => s.selectStation)

  const { data: stations } = useStations()
  const { data: towns } = useTowns()
  const { data: alerts } = useAlerts({ limit: 1000, town_code: townCode || null })

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

  // 選了站 → 飛過去：zoom 太小（大範圍看不清）或站不在視野內都飛
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!ready || !map || !selectedUid) return
    const st = stations?.find((s) => s.uid === selectedUid)
    if (!st || !hasCoord(st)) return
    const z = map.getZoom()
    if (z < 12 || !map.getBounds().contains([st.lon, st.lat])) {
      map.flyTo({ center: [st.lon, st.lat], zoom: Math.max(z, 14), duration: 800 })
    }
  }, [selectedUid, ready, stations])

  return (
    <div className="relative h-full w-full">
      <MapGL
        ref={mapRef}
        reuseMaps
        mapStyle={MAP_STYLE}
        initialViewState={{ longitude: 121.46, latitude: 25.01, zoom: 10.4 }}
        interactiveLayerIds={INTERACTIVE_LAYER_IDS}
        onLoad={() => setReady(true)}
        onError={(e) => console.error('[CityMap]', e.error?.message ?? e)}
        onClick={onClick}
        onMouseMove={onMouseMove}
        onMouseLeave={() => setHover(null)}
        cursor={hover ? 'pointer' : 'default'}
        attributionControl={{ compact: true }}
        style={{ width: '100%', height: '100%' }}
      >
        <NavigationControl position="top-right" showCompass={false} />

        {/* 選區聚光燈：選中區以外變暗 */}
        <Source id="mask" type="geojson" data={mask}>
          <Layer
            id="district-mask"
            type="fill"
            paint={{ 'fill-color': '#0b0d10', 'fill-opacity': 0.45, 'fill-opacity-transition': { duration: 200 } } as never}
          />
        </Source>

        <Source id="city" type="geojson" data={outlineGeo as unknown as FeatureCollection}>
          <Layer {...DISTRICT_LAYERS.cityOutline(townName)} />
        </Source>

        <Source id="districts" type="geojson" data={districtsGeo as unknown as FeatureCollection}>
          <Layer {...DISTRICT_LAYERS.fill(townName)} />
          <Layer {...DISTRICT_LAYERS.line()} />
          <Layer {...DISTRICT_LAYERS.lineSel(townName)} />
        </Source>

        <Source id="stations" type="geojson" data={geojson}>
          <Layer {...STATION_LAYERS.glow(townName)} />
          <Layer {...STATION_LAYERS.pulse(townName)} />
          <Layer {...STATION_LAYERS.base(townName, selectedUid)} />
          <Layer {...STATION_LAYERS.alert(townName, selectedUid)} />
          <Layer {...STATION_LAYERS.selected(selectedUid)} />
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

      <div className="pointer-events-none absolute right-2 top-2 z-10 rounded-xs border border-edge bg-bg/85 px-[14px] py-[10px] text-[0.72rem] leading-[1.9] tracking-[0.06em] text-ink2 backdrop-blur">
        <div className="flex items-center gap-[10px]">
          <span className="inline-block size-[11px] rounded-full bg-hot" />缺車風險
        </div>
        <div className="flex items-center gap-[10px]">
          <span className="inline-block size-[11px] rounded-full border-[2px] border-cold bg-transparent" />滿站風險
        </div>
        <div className="flex items-center gap-[10px]">
          <span className="inline-block size-[11px] rounded-full border border-white/70 bg-white/10" />供需健康
        </div>
        <div className="mt-[6px] flex items-center gap-[10px] border-t border-hair pt-[6px] text-ink3">
          <span className="relative inline-flex size-[11px] items-center justify-center">
            <span className="absolute inline-block size-[11px] animate-ping rounded-full bg-hot/60" />
            <span className="inline-block size-[6px] rounded-full bg-hot" />
          </span>
          高風險
        </div>
        <div className="flex items-center gap-[10px] text-ink3">
          <span className="inline-block size-[11px] rounded-full border-2 border-white" />
          已選取
        </div>
      </div>
    </div>
  )
}
