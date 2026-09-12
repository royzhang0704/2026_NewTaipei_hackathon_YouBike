-- ════════════════════════════════════════════════════════════
-- 70_dispatch_order.sql —— 建調度單表 hackathon_backend_dispatch_order
--
--   風險判定（risk_snapshot）只說「這站要補 6 台」，說完就沒了 ——
--   沒有地方記「誰去補、從哪裡調、調了沒」。這張表補的就是那一段：
--   從「建議」到「已派」。
--
-- ★ 定位與 risk_snapshot 相反：那張是「可重建的衍生快取」，可隨時
--   TRUNCATE 重灌；這張是**使用者按下確認的事實**，重算不出來。
--   所以一律軟刪（status='invalid'），不 DELETE。
--   （demo 之間要清桌面請用檔尾的 TRUNCATE，那是 demo 行為不是維運行為。）
--
-- ★ 沒有 TTL（2026-09-12 定案）：active 單只會被 jobs/dispatch_sweep
--   依風險現況收掉，或被人工撤銷。demo 前清表是配套。
--
-- 疊加表（同 40_ / 61_）：一律 IF NOT EXISTS，重跑不清空。
-- 未設 FK to station：主檔可能被除名（同 risk_snapshot 的理由）。
--
-- 出處：meet/20260912/計劃-調度確認.md §3（16 題訪談定案）
--
-- 用法：
--   PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
--        -v ON_ERROR_STOP=1 -f code_backend/sql/70_dispatch_order.sql
-- ════════════════════════════════════════════════════════════
\timing on

BEGIN;

CREATE TABLE IF NOT EXISTS public.hackathon_backend_dispatch_order (
  id            bigserial   PRIMARY KEY,
  -- ── 這趟車 ──
  from_uid      text        NOT NULL,
  to_uid        text        NOT NULL,
  bikes         int         NOT NULL,
  distance_m    int,
  -- ── 站在誰的立場 ──
  anchor_uid    text        NOT NULL,
  action        text        NOT NULL,
  -- ── 生命週期 ──
  status        text        NOT NULL DEFAULT 'active',
  closed_reason text,
  -- ── 時間（★ 一律虛擬時鐘）──
  created_slot  timestamp   NOT NULL,
  closed_slot   timestamp,
  -- ── 溯源 ──
  origin        timestamp   NOT NULL,
  operator      text        NOT NULL DEFAULT 'IM_TEST'
);

-- ★ 同一對 (來源, 目的) 同時只能有一筆進行中的單。
--   部分唯一索引（WHERE status='active'）—— 收掉的舊單不佔位，
--   同一對站之後還能再派。重複確認走 ON CONFLICT 覆蓋台數，不是錯誤。
CREATE UNIQUE INDEX IF NOT EXISTS hackathon_backend_dispatch_order_active_pair_idx
  ON public.hackathon_backend_dispatch_order (from_uid, to_uid) WHERE status = 'active';

-- 兩個主查詢都吃這支：①地圖／清單撈全部 active ②行動卡算「已調度 N 台」
CREATE INDEX IF NOT EXISTS hackathon_backend_dispatch_order_status_anchor_idx
  ON public.hackathon_backend_dispatch_order (status, anchor_uid);

COMMENT ON TABLE public.hackathon_backend_dispatch_order IS
  '★ 調度單：使用者在單站行動卡按下「確認調度」後寫入一筆，記「從 A 站調 N 台到 B 站」。與 risk_snapshot 的關係是「建議 → 已派」—— 那張表每輪重算可丟棄，這張是使用者的決定，重算不出來，一律軟刪不 DELETE。由 jobs/dispatch_sweep（batch_predict 階段三）依最新風險現況收成 fulfilled／invalid。無 TTL，demo 前清表。';

COMMENT ON COLUMN public.hackathon_backend_dispatch_order.id IS
  '流水號。前端撤銷（DELETE /dispatch/orders/{id}）與地圖高亮（highlightOrderId）都用這個。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.from_uid IS
  '車的來源站（車從這裡被載走）。★ 不是「風險站」—— 哪一端是風險站看 anchor_uid。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.to_uid IS
  '車的目的站（車被載到這裡）。★ 同上，方向是車的方向，不是風險的方向。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.bikes IS
  '★ 本筆台數。下界 2（config.DISPATCH_MIN_BIKES）—— 1 台的候選會讓清單塞滿零星站，湊滿一次要跑五六站，文案也難寫。上界 = 來源站最新一輪的 supply（已扣掉其他 active 單的承諾量），由服務層在寫入前重驗；超量整批 400 不做 clamp。供給側用 floor 不用 ceil：多搬一台會讓來源站掉到常態水位以下，下一輪可能反被判缺車。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.distance_m IS
  '★ 確認當下的直線距離快照（haversine，app/geo.py）。存快照不即時算 —— 主檔座標之後若修，歷史單的距離不該跟著變。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.anchor_uid IS
  '★ 使用者當初點的那個站（= 風險站，發起這次調度的理由）。光看 from/to 分不出哪端是風險站 —— 兩端都可能有燈（候選站本身是滿站高風險時，一趟車解決兩站，那是加分情境）。dispatch_sweep 的兩條規則靠這一欄分邊：規則①查 anchor 那端是否已無同方向需求，規則②查**非 anchor** 那端是否轉成反方向風險。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.action IS
  '★ refill（補車）／remove（取車），**站在 anchor 的立場**。refill 單：anchor = to_uid（缺車站等車來）；remove 單：anchor = from_uid（滿站要把車清走）。三處判準（選候選／發起資格／排程收單）一律用 action 不用 level —— 目標站從 high 掉到 mid 按 level 就算「離開高風險」，但它其實還缺 5 台、車還在路上。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.status IS
  '★ active（進行中）／fulfilled（anchor 已無同方向需求）／invalid（另一端失效或人工撤銷）。軟刪不 DELETE —— 「刪除結果統整要記 log」一支 SELECT 就查得出來。⚠ fulfilled **不代表車真的到了**，只代表 anchor 已無同方向需求；對外說法不可講成「調度成功率」。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.closed_reason IS
  '逐筆收單理由（log 兩層的細層）：如「目標站 板橋國中站 已無補車需求」／「供給站 中和國小站 轉為缺車風險（mid）」／「人工撤銷（IM_TEST）」。整輪統計在 job_run(job_name=''dispatch_sweep'').detail，不另開表。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.created_slot IS
  '★ 確認當下的**虛擬時鐘**（sys_config_repo.effective_now()），不是真實時間。demo 回放下真實時間無意義 —— 使用者看到的、文案講的、地圖標的都是虛擬時刻。要查「哪一次 demo 產的」請靠 origin 或 id 區間。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.closed_slot IS
  '★ 被 dispatch_sweep 收掉／人工撤銷的虛擬時刻。同 created_slot，只有虛擬時鐘。active 時為 NULL。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.origin IS
  '★ 產生這筆建議的 risk_snapshot.origin，**只做溯源，不是寫入門檻**。原本想用「origin 必須等於最新一輪」擋過期確認，但 60x 回放下一格 = 30 真實秒，使用者讀完文案再勾選早已跨輪 → 幾乎必定失敗。改成「只有 1x 才開放調度」（前端按鈕層級禁用）＋ 寫入時用最新一輪重驗 supply。';
COMMENT ON COLUMN public.hackathon_backend_dispatch_order.operator IS
  '★ 操作者，**不可信**。前端 VITE_AUTH_MODE=mock、後端沒有 /auth/*，token 是 mock.{account}.{ts}，沒有任何可信來源。本欄一律由後端寫死 IM_TEST，不接受前端傳值。畫面上「IM_TEST 10:05 確認」只是 demo 呈現。';

COMMIT;

ANALYZE public.hackathon_backend_dispatch_order;

-- ══ 驗收 ═════════════════════════════════════════════════════
\echo
\echo '── 表是否建起來（期望 1 列、13 欄）'
SELECT c.relname AS 表名,
       (SELECT count(*) FROM pg_attribute a
         WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS 欄數,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS 大小
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'hackathon_backend_dispatch_order';

\echo
\echo '── PK / 索引（期望 3 支：pkey + active_pair_idx + status_anchor_idx）'
SELECT indexname AS 索引名, indexdef AS 定義
FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'hackathon_backend_dispatch_order'
ORDER BY 1;

\echo
\echo '── 註解覆蓋率（缺註解欄數應為 0）'
SELECT count(*) AS 欄數,
       count(*) FILTER (WHERE col_description(c.oid, a.attnum) IS NULL) AS 缺註解,
       CASE WHEN obj_description(c.oid, 'pg_class') IS NULL THEN '✗' ELSE '✓' END AS 表註解
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relname = 'hackathon_backend_dispatch_order'
GROUP BY c.oid;

\echo
\echo '── 現有列數（首次建表應為 0）'
SELECT count(*) FILTER (WHERE status = 'active')    AS active,
       count(*) FILTER (WHERE status = 'fulfilled') AS fulfilled,
       count(*) FILTER (WHERE status = 'invalid')   AS invalid,
       count(*) AS 合計
FROM public.hackathon_backend_dispatch_order;

-- ── demo 清表（不做 TTL 的配套；跑完一輪 demo 後手動執行）────────
--   TRUNCATE public.hackathon_backend_dispatch_order RESTART IDENTITY;
