#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════
# 30_load_cat_map.sh —— 把 DeepAR 的 cat 編號灌進 station 表
#
# ★ 這是整個後端最容易安靜出錯的一步。
#   DeepAR 只認編號不認站名，編號由訓練時 to_series() 的字典序決定。
#   猜錯不會報錯 —— 每一站都拿到別站的預測，數字看起來完全合理。
#
# 唯一來源：$SRC/cat_map.csv，預設 ml-deepar/data/sagemaker_demo2604
#   （8/31 demo2604：1,528 站，names_sha a14aff9be3e733bc）
#
# ★ 用 SRC 環境變數換來源，不要改這支：
#     SRC=ml-deepar/data/sagemaker_demo2604_v2 bash backend/sql/30_load_cat_map.sh
#
# ★ 9/10 d2604v2-r2：train channel 與 demo2604 逐位元組相同，
#   所以 cat_map.csv 也逐位元組相同（md5 0f6ea4b47e57a82b04ceccaa1f9397a4）。
#   換到 r2 時這一步實際上是 no-op —— 但照跑，因為「no-op」要由
#   驗收數字證明，不是由推論證明。
#
# ★★ 換模型必先全清再灌 —— cat 編號是「這一份訓練資料」的字典序，
#   換一份對照表，掉出去的站若留著舊編號就是安靜的 cat 錯配
#   （每一站拿到別站的預測，數字看起來完全合理）。
#
# 用法：
#   bash backend/sql/30_load_cat_map.sh --verify   # 只驗來源檔，不碰 DB
#   bash backend/sql/30_load_cat_map.sh            # 驗 + 灌 + 驗收
# ════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/../.."          # 專案根

SRC="${SRC:-ml-deepar/data/sagemaker_demo2604}"
PORT=5433
export PGPASSWORD=postgres
PSQL="psql -h 127.0.0.1 -p $PORT -U postgres -d youbike -v ON_ERROR_STOP=1"

# ── 1　驗來源檔（不碰 DB）──────────────────────────────────
echo "── 來源 $SRC"
echo "── 驗 $SRC/cat_map.csv 與 meta.json"
python3 - "$SRC" <<'PY'
import json, csv, hashlib, sys, pathlib
d = pathlib.Path(sys.argv[1])
m = json.loads((d / 'meta.json').read_text(encoding='utf-8'))
names = m['names']

sha = hashlib.sha1("\n".join(names).encode()).hexdigest()[:16]   # train_local.py:125
if sha != m['names_sha']:
    sys.exit(f"✗ names_sha 對不上：重算 {sha} vs meta {m['names_sha']}")
if len(names) != m['names_n']:
    sys.exit(f"✗ names_n 對不上：{len(names)} vs {m['names_n']}")
print(f"   ✓ meta.json  {m['names_n']} 站  names_sha {sha}")

rows = list(csv.DictReader((d / 'cat_map.csv').open(encoding='utf-8')))
if len(rows) != len(names):
    sys.exit(f"✗ cat_map.csv {len(rows)} 列 vs names {len(names)} 站")
bad = [(i, r) for i, r in enumerate(rows)
       if int(r['cat']) != i or r['station_uid'] != names[i]]
if bad:
    sys.exit(f"✗ cat_map.csv 有 {len(bad)} 列與 names 不符，前三筆 {bad[:3]}")
print(f"   ✓ cat_map.csv {len(rows)} 列，逐列比對 names[cat] == station_uid 全數相符")
PY

[ "${1:-}" = "--verify" ] && { echo "（--verify 模式，不碰 DB）"; exit 0; }

# ── 2　灌入 ────────────────────────────────────────────────
echo
echo "── 灌進 hackathon_backend_station.cat"
$PSQL <<SQL
BEGIN;
CREATE TEMP TABLE _cat_map (cat int, station_uid text) ON COMMIT DROP;
\copy _cat_map FROM '$SRC/cat_map.csv' WITH (FORMAT csv, HEADER true)

-- ★ 先全清：不在新對照表裡的站必須回到 NULL，不能留舊模型的編號
UPDATE public.hackathon_backend_station SET cat = NULL WHERE cat IS NOT NULL;

UPDATE public.hackathon_backend_station s
   SET cat = m.cat
  FROM _cat_map m
 WHERE s.station_uid = m.station_uid;

-- ★★ 護欄一：危險的方向 —— 主檔的站拿到「錯的」cat。
--   上面的 UPDATE 用 station_uid join，理論上錯不了，但這是整個後端
--   唯一會安靜錯的一步（每站拿到別站的預測，數字看起來完全合理），
--   所以明確驗一次。0 才過，否則整筆 rollback。
DO \$\$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n
    FROM public.hackathon_backend_station s
    JOIN _cat_map m USING (station_uid)
   WHERE s.cat IS DISTINCT FROM m.cat;
  IF n > 0 THEN
    RAISE EXCEPTION '% 站的 cat 與 cat_map 不符 —— 灌入沒有生效', n;
  END IF;
END
\$\$;

-- ★ 護欄二：良性的方向 —— cat_map 有、主檔沒有。
--   那表示模型認識一個我們已經不再服務的站，該編號成為未使用值，
--   不會有任何站去要它，也不影響任何預測。
--
--   ⚠ 2026-09-10 放寬：原本這裡是 EXCEPTION，實測擋下了一次合法的灌入 ——
--   NWT500223025（cat 1002）在 2026-05-01 前就消失，被
--   meet/20260904/清理-只留5月1日站點.sh 從主檔移除，但它在訓練期存在
--   （baseline_grid 仍有 1,420 列），所以必然留在 cat_map 裡。
--   這是**預期會發生**的狀態，不是資料不同步。
--
--   保留上限：少量放行並印警告，超過 ORPHAN_LIMIT 才視為真的不同步
--   （例如灌錯了一份別的模型的 cat_map）。
DO \$\$
DECLARE n int; lim int := 5;
BEGIN
  SELECT count(*) INTO n
    FROM _cat_map m
    LEFT JOIN public.hackathon_backend_station s USING (station_uid)
   WHERE s.station_uid IS NULL;
  IF n > lim THEN
    RAISE EXCEPTION 'cat_map 有 % 站不在主檔（上限 %）—— 兩份資料真的不同步', n, lim;
  ELSIF n > 0 THEN
    RAISE WARNING 'cat_map 有 % 站不在主檔，這些編號未被使用（在上限 % 內，放行）', n, lim;
  END IF;
END
\$\$;
COMMIT;
SQL

# ── 3　驗收 ────────────────────────────────────────────────
echo
# ⚠ 9/10 實測：主檔已被 meet/20260904/清理-只留5月1日站點.sh 修剪成 1,538 站，
#   舊註解寫的「1600 / 72 NULL」已過期。現況是 1527 有 cat / 11 無 cat / 1538 全部
#   —— cat_map 的 1,528 站裡有一站（NWT500223025，cat 1002）在 5/1 前就消失，
#   已不在主檔內。那 11 站是訓練後新增的，全部有鄰站代理。
echo "── 驗收（現況預期：1527 有 cat / 11 無 cat / 1538 全部 / cat 0~1527）"
$PSQL -c "
SELECT count(*) FILTER (WHERE cat IS NOT NULL) AS 有cat,
       count(*) FILTER (WHERE cat IS NULL)     AS 無cat,
       count(*)                                AS 全部,
       min(cat) AS cat最小, max(cat) AS cat最大
FROM public.hackathon_backend_station"

echo
echo "── 沒有 cat 的站（這些會回 STATION_UNKNOWN，訓練後才新增）"
$PSQL -c "
SELECT town, count(*) AS 站數
FROM public.hackathon_backend_station
WHERE cat IS NULL GROUP BY town ORDER BY 2 DESC, 1"
