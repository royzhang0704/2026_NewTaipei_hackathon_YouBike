#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════
# 30_load_cat_map.sh —— 把 DeepAR 的 cat 編號灌進 station 表
#
# ★ 這是整個後端最容易安靜出錯的一步。
#   DeepAR 只認編號不認站名，編號由訓練時 to_series() 的字典序決定。
#   猜錯不會報錯 —— 每一站都拿到別站的預測，數字看起來完全合理。
#
# 唯一來源：ml-deepar/data/sagemaker_demo2604/cat_map.csv
#   （8/31 換 demo2604 模型：1,528 站，names_sha a14aff9be3e733bc；
#     這份 meta.json 是匯出時新產的，H=6 等組態可信）
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

SRC=ml-deepar/data/sagemaker_demo2604
PORT=5433
export PGPASSWORD=postgres
PSQL="psql -h 127.0.0.1 -p $PORT -U postgres -d youbike -v ON_ERROR_STOP=1"

# ── 1　驗來源檔（不碰 DB）──────────────────────────────────
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

-- ★ 護欄：cat_map 裡有、但 station 表沒有的站 —— 表示兩份資料不同步，
--   灌下去會有站永遠拿不到 cat。不通過就整筆 rollback
DO \$\$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n
    FROM _cat_map m
    LEFT JOIN public.hackathon_backend_station s USING (station_uid)
   WHERE s.station_uid IS NULL;
  IF n > 0 THEN
    RAISE EXCEPTION 'cat_map 有 % 站不在 station 表裡 —— 兩份資料不同步', n;
  END IF;
END
\$\$;
COMMIT;
SQL

# ── 3　驗收 ────────────────────────────────────────────────
echo
echo "── 驗收（demo2604 預期 1528 有 cat / 主檔 1600 / 72 NULL）"
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
