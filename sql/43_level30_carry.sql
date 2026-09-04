-- ════════════════════════════════════════════════════════════
-- 43_level30_carry.sql —— baseline_grid 4~7 月 → level30，缺格無限 carry
--
-- 出處：meet/20260902/計劃-level30灌歷史與無限carry.md（13 項決策）
--       推翻 meet/20260901/現況-斷訊站處理決策.md 的建議 A，改用提案 C。
--
-- ★ 這支與 40/42 的「疊加不重建」原則相反，它是**重建**：
--   04-01 ~ 08-01 這個區間的權威來源就是 baseline_grid，先 DELETE 再全量
--   灌，重跑 N 次 = 跑 1 次。8 月之後的真排程資料在範圍外，一律不碰。
--
-- 四條規則（少任何一條結果都會失真）：
--   1. 每站的起點 = 窗內首個 avail IS NOT NULL 的格。之前的 26,192 格
--      （89 站）不寫列 —— 無限 carry 沒有前值可用，「從該站有車輛水位
--      開始」是使用者的原話。
--   2. 每站的終點 = 該站在窗內的最後一格，不延長到共同終點。
--      t1 之後不是洞，是「這站在資料裡沒有了」。20 站停在 07-10、
--      64 站停在 07-26~30、473 站停在 07-31 尾巴，全部不補。
--   3. 洞一律無限 carry，不設上限（baseline_grid 自己的規則是 >6 格留
--      NULL，這支把那批 NULL 全部填掉）。
--   4. docks 跟著一起 carry，但用**自己的** island 編號（見下面 ⚠）。
--
-- ★ is_imputed 在這裡變三態（42_level30_is_imputed.sql 的註解是權威出處）：
--     0  實測，或 baseline_grid 自己填的 ≤6 格 carry
--     1  週期補值（取一週前同 slot）
--     2  ★ 本檔填的無限 carry
--   1 與 2 互不覆蓋；真值一律可以覆蓋兩者。
--
-- ⚠ 這支灌完之後，服務層有三個必然變化，不是 bug（計劃 §4）：
--     replay_pull 的覆蓋率門檻永遠會過（job_run 不再 skipped）
--     batch_predict 的 STALE_ANCHOR 歸零（68 站回到清單，正是要的）
--     risk 的 now_carried 恆為 false（決策 10 定不修）
--
-- ⚠⚠ retention 的衝突：config.py 的 LEVEL30_RETENTION_DAYS = 14
--   目前只是常數、全 backend 零使用。計劃-排程自癒 §2 說它屬「階段②」。
--   ★ 那支一實作，這裡灌的 4 個月會被刪剩 14 天。實作 retention 時
--     必須把下界卡在 '2026-08-01'，讓歷史區豁免。
--
-- 用法：
--   PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
--        -v ON_ERROR_STOP=1 -f backend/sql/43_level30_carry.sql
-- ════════════════════════════════════════════════════════════
\set ON_ERROR_STOP on
\timing on

\set T0 '2026-04-01'
\set T1 '2026-08-01'

\echo ''
\echo '── 灌之前的現況'
SELECT count(*) AS 總列, count(*) FILTER (WHERE slot >= :'T0' AND slot < :'T1') AS 將被重建,
       count(*) FILTER (WHERE slot >= :'T1') AS 八月不動
FROM public.hackathon_backend_level30;

BEGIN;

DELETE FROM public.hackathon_backend_level30
 WHERE slot >= :'T0' AND slot < :'T1';

WITH w AS (
  SELECT station_uid, slot, avail, docks, is_observed
    FROM public.baseline_grid
   WHERE slot >= :'T0' AND slot < :'T1'
), f AS (
  -- 規則 1：每站的起點。fv IS NULL（整窗沒有任何水位）的站，下面的
  -- `slot >= f.fv` 會整站落空 —— 這正是要的，不必另外寫排除條件。
  SELECT station_uid, min(slot) FILTER (WHERE avail IS NOT NULL) AS fv
    FROM w GROUP BY station_uid
), g AS (
  -- island 編號：每遇到一格非 NULL 就 +1，同一 grp 內共用那格的值。
  -- 招式沿用 baseline_resample_export.sh 的 `grp`，差別是那裡有 6 格上限，
  -- 這裡沒有。
  --
  -- ⚠ avail 與 docks 必須各自編號，不能共用。兩者的洞高度重合
  --   （125,184 個 docks NULL 有 125,052 個與 avail NULL 重疊），但剩下
  --   132 格是「avail 有值、docks 為 NULL」—— 共用 avail 的編號時，
  --   那 132 格會各自開一個新 island 且首格 docks 是 NULL，
  --   整段 island 的 docks 就全被 carry 成 NULL。
  --
  -- ⚠ window function 不能巢狀寫進 WINDOW 定義裡（PG 會報
  --   "window functions are not allowed in window definitions"），
  --   所以編號與取值一定要拆成 g / c 兩層 CTE。
  SELECT station_uid, slot, avail, docks, is_observed,
         count(avail) OVER pw AS grp_a,
         count(docks) OVER pw AS grp_d
    FROM w
  WINDOW pw AS (PARTITION BY station_uid ORDER BY slot ROWS UNBOUNDED PRECEDING)
), c AS (
  SELECT station_uid, slot, is_observed,
         avail AS raw,          -- ★ 留著判斷這格是不是補出來的
         first_value(avail) OVER (PARTITION BY station_uid, grp_a ORDER BY slot) AS avail_c,
         first_value(docks) OVER (PARTITION BY station_uid, grp_d ORDER BY slot) AS docks_c
    FROM g
)
INSERT INTO public.hackathon_backend_level30
       (station_uid, slot, avail, docks, is_observed, is_imputed)
SELECT c.station_uid, c.slot, c.avail_c, c.docks_c,
       -- is_observed 直接抄。不必特別處理補出來的格：baseline_grid 的
       -- 斷言保證 is_observed=1 ⟺ gap_slots=0 ⟹ avail 非 NULL，
       -- 所以 raw IS NULL 的格 is_observed 必然已經是 0。
       c.is_observed,
       CASE WHEN c.raw IS NULL THEN 2 ELSE 0 END::smallint
  FROM c JOIN f USING (station_uid)
 WHERE c.slot >= f.fv;                          -- 規則 1

COMMIT;

-- ★ DELETE 208 萬列 + INSERT 902 萬列之後統計值完全過時，
--   不 ANALYZE 的話 history_repo.tail() 的計劃可能走錯。
ANALYZE public.hackathon_backend_level30;

-- ⚠ 若上面的單一交易因 WAL 撐不住而失敗（max_wal_size=8GB），改按月分四刀。
--   ★ 分刀時 CTE 的 w 仍然要涵蓋**整個窗**，只有最後的 INSERT 加
--     `AND c.slot >= '2026-05-01' AND c.slot < '2026-06-01'` 這種條件 ——
--     carry 會跨月（5/1 00:00 的前值可能在 4/30 23:30），
--     把 w 也切成單月的話每個月的第一段 island 都會從零重算，全錯。


-- ══ 驗收 ═════════════════════════════════════════════════════
\timing off

\echo ''
\echo '### 1 列數與站數 —— 應為 9,024,550 / 1,585'
SELECT count(*) AS 列, count(DISTINCT station_uid) AS 站,
       min(slot) AS 起, max(slot) AS 迄
FROM public.hackathon_backend_level30 WHERE slot >= :'T0' AND slot < :'T1';

\echo ''
\echo '### 4 旗標分佈 —— is_imputed=2 應為 792,237 格'
SELECT is_observed, is_imputed, count(*) AS 格數
FROM public.hackathon_backend_level30
WHERE slot >= :'T0' AND slot < :'T1'
GROUP BY 1, 2 ORDER BY 1, 2;

\echo ''
\echo '### 7 八月真排程資料沒被動到 —— 應為 742,224 / 54,725'
SELECT count(*) AS 八月列, count(*) FILTER (WHERE is_imputed = 1) AS 週期補值
FROM public.hackathon_backend_level30 WHERE slot >= :'T1';

\echo ''
\echo '### ★ 機器斷言 —— 全部必須是 t'
SELECT
  -- 2 決策 2：值不缺。avail 與 docks 一格都不准是 NULL
  (SELECT count(*) FROM public.hackathon_backend_level30
    WHERE slot >= :'T0' AND slot < :'T1'
      AND (avail IS NULL OR docks IS NULL)) = 0
    AS 零NULL,
  -- 3 每站格點連續無跳號。沿用 baseline_resample_export.sh 的同一條斷言
  (SELECT count(*) FROM (
     SELECT station_uid, count(*) AS n,
            (EXTRACT(EPOCH FROM (max(slot) - min(slot))) / 1800 + 1)::bigint AS expect
       FROM public.hackathon_backend_level30
      WHERE slot >= :'T0' AND slot < :'T1'
      GROUP BY 1) d WHERE n <> expect) = 0
    AS 每站格點連續無跳號,
  -- 5 ★ 真值必須與 baseline_grid 逐格相同。只有 is_imputed=2 的格准不一樣
  --
  -- ⚠ docks 的條件多一層 `b.docks IS NOT NULL`，不是偷懶。is_imputed 只
  --   表達 **avail** 的來源，不表達 docks 的 —— 有 132 格是「avail 有值
  --   （所以 is_imputed=0）、但 baseline_grid 的 docks 是 NULL」，那批的
  --   docks 依決策 6 被 carry 補了值。不加這層條件，這 132 格會讓斷言
  --   永遠是 f（9/2 首次執行就踩到了）。
  --   真正要抓的是「兩邊都有值卻不一樣」= carry 算錯或錯位。
  (SELECT count(*) FROM public.hackathon_backend_level30 l
     JOIN public.baseline_grid b USING (station_uid, slot)
    WHERE l.slot >= :'T0' AND l.slot < :'T1' AND l.is_imputed = 0
      AND (l.avail       IS DISTINCT FROM b.avail
        OR l.is_observed IS DISTINCT FROM b.is_observed
        OR (b.docks IS NOT NULL AND l.docks IS DISTINCT FROM b.docks))) = 0
    AS 真值與來源逐格相同,
  -- 5c 上面放行的那批要單獨盯住：數量必須恰好是 132，且 avail 全部有值。
  --    變多就代表 carry 的 island 分組被動過（docks 必須用自己的編號）。
  (SELECT count(*) FROM public.hackathon_backend_level30 l
     JOIN public.baseline_grid b USING (station_uid, slot)
    WHERE l.slot >= :'T0' AND l.slot < :'T1' AND l.is_imputed = 0
      AND b.docks IS NULL) = 132
    AS docks補值格恰為132,
  -- 5b 反向：level30 不准有 baseline_grid 沒有的 (站, 格)
  (SELECT count(*) FROM public.hackathon_backend_level30 l
     LEFT JOIN public.baseline_grid b USING (station_uid, slot)
    WHERE l.slot >= :'T0' AND l.slot < :'T1' AND b.station_uid IS NULL) = 0
    AS 無來源外的列,
  -- 6 決策 5：每站終點沒有被憑空延長
  (SELECT count(*) FROM
     (SELECT station_uid, max(slot) AS t FROM public.hackathon_backend_level30
       WHERE slot >= :'T0' AND slot < :'T1' GROUP BY 1) l
     JOIN
     (SELECT station_uid, max(slot) AS t FROM public.baseline_grid
       WHERE slot >= :'T0' AND slot < :'T1' GROUP BY 1) b USING (station_uid)
    WHERE l.t <> b.t) = 0
    AS 終點未被延長,
  -- 決策 4：每站起點 = 窗內首個有水位的格
  (SELECT count(*) FROM
     (SELECT station_uid, min(slot) AS t FROM public.hackathon_backend_level30
       WHERE slot >= :'T0' AND slot < :'T1' GROUP BY 1) l
     JOIN
     (SELECT station_uid, min(slot) FILTER (WHERE avail IS NOT NULL) AS t
        FROM public.baseline_grid
       WHERE slot >= :'T0' AND slot < :'T1' GROUP BY 1) b USING (station_uid)
    WHERE l.t <> b.t) = 0
    AS 起點為首個有水位的格,
  -- 硬規則③ 的前提：is_imputed=2 的格全部是 is_observed=0
  (SELECT count(*) FROM public.hackathon_backend_level30
    WHERE slot >= :'T0' AND slot < :'T1'
      AND is_imputed = 2 AND is_observed <> 0) = 0
    AS 補值格皆非實測;

\echo ''
\echo '### 逐月概況'
SELECT to_char(slot, 'YYYY-MM') AS 月, count(*) AS 列,
       count(DISTINCT station_uid) AS 站,
       count(*) FILTER (WHERE is_observed = 1) AS 實測,
       count(*) FILTER (WHERE is_observed = 0 AND is_imputed = 0) AS 原生carry,
       count(*) FILTER (WHERE is_imputed = 2) AS 無限carry,
       round(100.0 * count(*) FILTER (WHERE is_imputed = 2) / count(*), 1) AS 補值pct
FROM public.hackathon_backend_level30
WHERE slot >= :'T0' AND slot < :'T1'
GROUP BY 1 ORDER BY 1;

\echo ''
\echo '### ★ 07-10~12 餵食故障區 —— 決策 9 不特殊處理，這裡只是讓它可見'
SELECT to_char(slot, 'MM-DD') AS 日, count(*) AS 格,
       round(100.0 * count(*) FILTER (WHERE is_imputed = 2) / count(*), 1) AS 補值pct
FROM public.hackathon_backend_level30
WHERE slot >= '2026-07-09' AND slot < '2026-07-14'
GROUP BY 1 ORDER BY 1;

\echo ''
\echo '完成。下一步：批② hist_repo.py:275 改 <> 0（不改的話 Job C 回補的真值蓋不掉 is_imputed=2）'
