#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════
# 31_load_proxy_cat.sh —— 為 cat IS NULL 的站灌「鄰站 cat 代理」
#
# 做法 A（meet/20260828/計劃-鄰站cat代理.md）：
#   借最近「有 cat 站」的 cat 編號，推論時仍餵本站自己的歷史。
#   本腳本只建代理關係（proxy_station_uid / proxy_distance_m 兩欄），
#   不動 cat 欄本身 —— cat 欄永遠只由 30_load_cat_map.sh 灌。
#
# ★ 這是故意製造 30 開頭警告的那種 cat 錯配（安靜、數字合理）。
#   服務層必須在回應標示 proxy 區塊 + caveat，沒有標示就不准上。
#
# 冪等：每次先把全表 proxy 欄清 NULL 再重算，重跑不累積。
#
# 用法：
#   bash backend/sql/31_load_proxy_cat.sh --verify   # 只查現況，不寫 DB
#   bash backend/sql/31_load_proxy_cat.sh            # 查 + 灌 + 驗收
# ════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/../.."          # 專案根

PORT=5433
export PGPASSWORD=postgres
PSQL="psql -h 127.0.0.1 -p $PORT -U postgres -d youbike -v ON_ERROR_STOP=1"

# ── 1　唯讀查核（--verify 到此為止）──────────────────────────
echo "── 現況查核（唯讀）"
$PSQL <<'SQL'
SELECT count(*) FILTER (WHERE cat IS NULL)     AS 無cat站,
       count(*) FILTER (WHERE cat IS NOT NULL) AS 代理候選,
       count(*) FILTER (WHERE cat IS NULL AND (lat IS NULL OR lon IS NULL))
                                               AS 無cat且無座標
FROM public.hackathon_backend_station;

-- 無座標的站找不到鄰站，有就要先補資料再跑
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n
    FROM public.hackathon_backend_station
   WHERE cat IS NULL AND (lat IS NULL OR lon IS NULL);
  IF n > 0 THEN
    RAISE EXCEPTION '有 % 個無 cat 站缺座標，無法找鄰站', n;
  END IF;
END
$$;
SQL

echo
echo "── 無 cat 站的歷史覆蓋（僅供參考：滿 48 格才會實際被代理救到）"
$PSQL <<'SQL'
SELECT count(*) FILTER (WHERE n >= 48)          AS 有滿48格,
       count(*) FILTER (WHERE n > 0 AND n < 48) AS 不足48格,
       count(*) FILTER (WHERE n = 0)            AS 完全無歷史
FROM (
  SELECT s.station_uid, count(l.slot) AS n
  FROM public.hackathon_backend_station s
  LEFT JOIN public.hackathon_backend_level30 l USING (station_uid)
  WHERE s.cat IS NULL
  GROUP BY s.station_uid
) t;
SQL

[ "${1:-}" = "--verify" ] && { echo "（--verify 模式，不寫 DB）"; exit 0; }

# ── 2　灌入 ────────────────────────────────────────────────
echo
echo "── 建欄位並灌代理關係"
$PSQL <<'SQL'
BEGIN;

ALTER TABLE public.hackathon_backend_station
  ADD COLUMN IF NOT EXISTS proxy_station_uid text,
  ADD COLUMN IF NOT EXISTS proxy_distance_m  int;

COMMENT ON COLUMN public.hackathon_backend_station.proxy_station_uid IS
  '★ 鄰站 cat 代理（做法 A）。只有 cat IS NULL 的站有值 = 最近「有 cat 站」的 uid；推論時借它的 cat、歷史仍用本站。由 31_load_proxy_cat.sh 灌，服務層回應必須標示 proxy 區塊。';
COMMENT ON COLUMN public.hackathon_backend_station.proxy_distance_m IS
  '到代理站的直線距離（公尺，haversine）。放進回應由下游取捨，>500m 代理品質存疑。';

-- 冪等：先全清再重算，範圍才不會外溢或累積
UPDATE public.hackathon_backend_station
   SET proxy_station_uid = NULL, proxy_distance_m = NULL
 WHERE proxy_station_uid IS NOT NULL OR proxy_distance_m IS NOT NULL;

-- 排序鍵用平方度距（排序一致即可），輸出值用 haversine
-- ★ LATERAL 不能直接引用 UPDATE 目標表（已實測會報
--   invalid reference to FROM-clause entry），所以先在子查詢裡
--   自連結算好 44 筆對照，再 join 回去更新
UPDATE public.hackathon_backend_station n
   SET proxy_station_uid = m.proxy_uid,
       proxy_distance_m  = m.dist_m
  FROM (
    SELECT t.station_uid, p.station_uid AS proxy_uid, p.dist_m
    FROM public.hackathon_backend_station t
    CROSS JOIN LATERAL (
      SELECT s.station_uid,
             round(6371000 * 2 * asin(sqrt(
               sin(radians(s.lat - t.lat)/2)^2 +
               cos(radians(t.lat)) * cos(radians(s.lat))
                 * sin(radians(s.lon - t.lon)/2)^2)))::int AS dist_m
      FROM public.hackathon_backend_station s
      WHERE s.cat IS NOT NULL
      ORDER BY (s.lat - t.lat)^2 + (s.lon - t.lon)^2
      LIMIT 1
    ) p
    WHERE t.cat IS NULL
  ) m
 WHERE n.station_uid = m.station_uid;

-- ★ 護欄：不通過就整筆 rollback
DO $$
DECLARE n int;
BEGIN
  -- (1) 無 cat 站必須全部拿到代理
  SELECT count(*) INTO n FROM public.hackathon_backend_station
   WHERE cat IS NULL AND proxy_station_uid IS NULL;
  IF n > 0 THEN
    RAISE EXCEPTION '護欄(1)：% 個無 cat 站沒拿到代理', n;
  END IF;

  -- (2) 代理站自己必須有 cat（否則服務層借到 NULL）
  SELECT count(*) INTO n
    FROM public.hackathon_backend_station a
    JOIN public.hackathon_backend_station p
      ON p.station_uid = a.proxy_station_uid
   WHERE p.cat IS NULL;
  IF n > 0 THEN
    RAISE EXCEPTION '護欄(2)：% 筆代理指向無 cat 的站', n;
  END IF;

  -- (3) 有 cat 的站不得有代理（範圍不外溢）
  SELECT count(*) INTO n FROM public.hackathon_backend_station
   WHERE cat IS NOT NULL AND proxy_station_uid IS NOT NULL;
  IF n > 0 THEN
    RAISE EXCEPTION '護欄(3)：% 個有 cat 站被誤填代理', n;
  END IF;

  -- (4) 不得自己代理自己
  SELECT count(*) INTO n FROM public.hackathon_backend_station
   WHERE proxy_station_uid = station_uid;
  IF n > 0 THEN
    RAISE EXCEPTION '護欄(4)：% 個站代理指向自己', n;
  END IF;
END
$$;

COMMIT;
SQL

# ── 3　驗收 ────────────────────────────────────────────────
echo
echo "── 驗收摘要（預期 44 站有代理；距離 >500m 預期 9 站）"
$PSQL <<'SQL'
SELECT count(*)                                    AS 有代理,
       count(*) FILTER (WHERE proxy_distance_m > 500) AS "超過500m",
       min(proxy_distance_m) AS 最近, max(proxy_distance_m) AS 最遠
FROM public.hackathon_backend_station
WHERE proxy_station_uid IS NOT NULL;
SQL

echo
echo "── 44 列清單（⚠ = 距離 >500m；歷史滿48格才會實際被救到，其餘仍回 INSUFFICIENT_HISTORY）"
$PSQL <<'SQL'
SELECT CASE WHEN n.proxy_distance_m > 500 THEN '⚠' ELSE '' END AS 標記,
       n.station_uid, n.station_name, n.town,
       n.proxy_station_uid, p.station_name AS 代理站名,
       n.proxy_distance_m AS 距離m,
       CASE WHEN h.n >= 48 THEN '✓' ELSE '✗ ' || h.n || '格' END AS 歷史滿48
FROM public.hackathon_backend_station n
JOIN public.hackathon_backend_station p ON p.station_uid = n.proxy_station_uid
LEFT JOIN LATERAL (
  SELECT count(*) AS n FROM public.hackathon_backend_level30 l
  WHERE l.station_uid = n.station_uid
) h ON true
WHERE n.proxy_station_uid IS NOT NULL
ORDER BY n.proxy_distance_m DESC;
SQL
