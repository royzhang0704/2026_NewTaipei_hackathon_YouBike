#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════
# 10_restore_source.sh —— 從 pgdump 還原後端要的來源表
#
# 為什麼不做全庫還原：
#   dump 共 95,399,918 行，其中 baseline(4,095 萬，raw 逐次快照)與
#   天氣 5 張(142 萬)後端一行都用不到 —— 合計 4,238 萬行 = 44.4%。
#
# 為什麼不用行號切段：
#   行號算錯一行就整段錯位，而且要自己處理 pre-data / post-data 的順序。
#   改用串流過濾：認 COPY 標頭 → 跳到 \. 為止，psql 只跑一次，
#   表結構 / 註解 / 索引 / PK 全部原樣建立，不要的表灌完再 DROP。
#
# ★ dump 是 pg_dumpall 的 plain SQL（不是 pg_restore 吃的 custom format），
#   自帶 CREATE ROLE youbike 與 CREATE DATABASE youbike，所以容器的 initdb
#   不能先建同名的 role/db —— POSTGRES_USER 用 postgres，不是 youbike。
#   （ml-deepar/sql/pg_up.sh 用 youbike，那支是給空庫用的，這裡不能沿用）
#
# 用法：
#   DRY=1 bash backend/sql/10_restore_source.sh    # 只印要做什麼
#   bash backend/sql/10_restore_source.sh          # 真的建容器 + 灌
# ════════════════════════════════════════════════════════════
set -euo pipefail

DUMP="${DUMP:-/Volumes/myPro/pgdump-20260827.sql.gz}"
CT=youbike-pg
PORT=5433
SUPER=postgres
SUPERPASS=postgres
IMG=docker.io/library/postgres:17-alpine   # dump 來自 17.11，對版
DRY="${DRY:-0}"

# 不還原的表 —— 資料段整段丟掉，表本身灌完再 DROP。
# ★ 清單寫死在 §2 的 awk regex 裡，不用 shell 變數 —— awk -v 賦值會再做一次
#   跳脫處理，把 \\( 吃成 (，regex 未配對而報 illegal primary。已實測。
#   baseline / wx_30 / wx_hourly / wx_neighbor / station_wx_map / dim_wx_station

export PGPASSWORD=$SUPERPASS
PSQL="psql -h 127.0.0.1 -p $PORT -U $SUPER"

run() { if [ "$DRY" = "1" ]; then echo "   $*"; else "$@"; fi; }

# ── 0　前置檢查 ─────────────────────────────────────────────
[ -f "$DUMP" ] || { echo "✗ 找不到 $DUMP"; exit 1; }
echo "── dump  $DUMP  ($(du -h "$DUMP" | cut -f1))"
# ★ head / grep -m1 會提早關管線，gzcat 收到 SIGPIPE，在 set -o pipefail 下
#   會讓整個腳本中止 —— 8/18 的 export_local.sh:53 已經記過這條教訓。
#   包一層 { … || true; } 讓 gzcat 的非零退出不算數
echo "   來源版本 $({ gzcat "$DUMP" || true; } | head -c 3000 \
                    | { grep -m1 'Dumped from' || true; } | sed 's/^-- //')"

# 檔尾必須有 complete，否則是截斷的備份。
# ⚠ tail 要讀到 EOF，所以這一步會完整解壓一次 791 MB，約 1~2 分鐘。
#   值得 —— 灌到一半才發現備份截斷，前面 20 分鐘就白花了
echo "   檢查檔尾完整性（要解壓整個檔案，約 1~2 分鐘）…"
if ! gzcat "$DUMP" | tail -3 | grep -q 'cluster dump complete'; then
  echo "✗ dump 檔尾沒有 'cluster dump complete' —— 備份可能截斷，中止"; exit 1
fi
echo "   ✓ 檔尾完整"

# ── 1　容器 ────────────────────────────────────────────────
echo
echo "── 建容器 ${CT}（postgres:17-alpine，:${PORT}）"
echo "   ⚠ 這會 rm -f 既有的 $CT 容器"
run podman machine start 2>/dev/null || true
run podman rm -f $CT 2>/dev/null || true
run podman run -d --name $CT \
  -e POSTGRES_USER=$SUPER -e POSTGRES_PASSWORD=$SUPERPASS \
  -e POSTGRES_DB=postgres \
  -e POSTGRES_INITDB_ARGS="--encoding=UTF8 --locale=C" \
  -p $PORT:5432 \
  -v youbike-pgdata:/var/lib/postgresql/data \
  --shm-size=1g \
  $IMG \
  -c shared_buffers=768MB -c work_mem=64MB -c maintenance_work_mem=512MB \
  -c max_wal_size=8GB -c checkpoint_timeout=30min -c synchronous_commit=off

if [ "$DRY" != "1" ]; then
  echo "   等待 PostgreSQL 就緒…"
  for _ in $(seq 1 60); do
    podman exec $CT pg_isready -U $SUPER -d postgres >/dev/null 2>&1 && break
    sleep 2
  done
  podman exec $CT pg_isready -U $SUPER -d postgres
fi

# ── 2　串流過濾 + 灌入 ──────────────────────────────────────
echo
echo "── 灌入（丟掉 baseline 與天氣 5 張的資料段）"
echo "   5,300 萬行，預估 15~40 分鐘"
if [ "$DRY" = "1" ]; then
  echo "   gzcat $DUMP | awk '<丟 baseline + 天氣 5 張>' | $PSQL -d postgres -v ON_ERROR_STOP=1 -f -"
else
  gzcat "$DUMP" \
  | awk '
      /^COPY public\.(baseline|wx_30|wx_hourly|wx_neighbor|station_wx_map|dim_wx_station) \(/ { s = 1 }
      s && /^\\\.$/ { s = 0; next }
      !s
    ' \
  | $PSQL -d postgres -v ON_ERROR_STOP=1 -f - > /tmp/restore.log 2>&1 \
  || { echo "✗ 灌入失敗，見 /tmp/restore.log"; tail -20 /tmp/restore.log; exit 1; }
  echo "   ✓ 灌入完成"
fi

# ── 3　DROP 那六張空表 ──────────────────────────────────────
echo
echo "── DROP 空表前的 view 清單（DROP 後要再比一次）"
run $PSQL -d youbike -c '\dv'

echo
echo "── DROP 六張空表"
# ⚠ CASCADE 會連帶砍依賴的 view。trainset_v 依賴 baseline_grid（留著）
#   與 dim_calendar（留著），8/25 已把 ⟨WEATHER⟩ JOIN 註解掉，所以安全 ——
#   但仍要用上下兩次 \dv 對照確認，不能只靠推理
run $PSQL -d youbike -c "DROP TABLE IF EXISTS
  public.baseline, public.wx_30, public.wx_hourly,
  public.wx_neighbor, public.station_wx_map, public.dim_wx_station CASCADE"

echo
echo "── DROP 之後的 view 清單"
run $PSQL -d youbike -c '\dv'

# ── 4　驗收 ────────────────────────────────────────────────
echo
echo "── 驗收"
run $PSQL -d youbike -c "
SELECT 'baseline_grid'   AS t, count(*) AS n, 26507029 AS 期望 FROM public.baseline_grid
UNION ALL SELECT 'tdx_grid30',      count(*), 26507029 FROM public.tdx_grid30
UNION ALL SELECT 'station_profile', count(*), 1594     FROM public.station_profile
UNION ALL SELECT 'dim_calendar',    count(*), 1461     FROM public.dim_calendar
ORDER BY 1"

echo
echo "下一步： psql -h 127.0.0.1 -p $PORT -U $SUPER -d youbike -f backend/sql/20_backend_ddl.sql"
