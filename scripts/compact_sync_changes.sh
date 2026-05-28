#!/usr/bin/env bash
#
# compact_sync_changes.sh — 一次性清理 BeeCount-Cloud SQLite DB 里 sync_changes
# 表的历史重复 / 已删 entity 的废弃 upsert。
#
# 背景:并发 fullPush bug(已修复,见 BeeCount #292)曾导致同一 entity_sync_id
# 在 sync_changes 表里有 N 倍重复 upsert(实测生产用户 tx 2.48x、account/
# category/tag 5-6x)。本脚本做两件事:
#   1) 对已 delete 的 entity,清掉它所有 upsert(保留 delete event)
#      —— 跟 BeeCount-Cloud PR #28 的 _compact_entity_upsert_events 同款逻辑,
#      只是对历史数据做 backfill
#   2) 对未 delete 的 entity,同 syncId 多条 upsert 只保留 MAX(change_id)
#      —— 最新那条 = 当前状态,更早的都是历史并发 push 留下的废纸
#   3) 修复 *_projection.source_change_id 指向(被删 change 的引用归到留下的 MAX)
#
# 安全性:
#   - 默认 --dry-run,把要删的 row 数和 projection 修复数算出来给你看,**不动 DB**
#   - 实际执行(--apply)前自动备份 DB(同目录加时间戳 .bak)
#   - 整个清理跑在一个事务里,任何 sanity check 失败自动 ROLLBACK
#   - sanity check:跑完后 projection.source_change_id **不应有 dangling**
#
# 用法:
#   ./compact_sync_changes.sh path/to/beecount.db                # dry-run
#   ./compact_sync_changes.sh path/to/beecount.db --apply        # 真做
#
# 跑前**强烈建议**:停掉相关服务 / 进维护窗口,避免跟正在 push 的客户端 race。

set -euo pipefail

# ─────────── 配色 ───────────
if [ -t 1 ]; then
  C_RED=$(printf '\033[31m')
  C_GREEN=$(printf '\033[32m')
  C_YELLOW=$(printf '\033[33m')
  C_BLUE=$(printf '\033[34m')
  C_BOLD=$(printf '\033[1m')
  C_RESET=$(printf '\033[0m')
else
  C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''; C_RESET=''
fi

die() { echo "${C_RED}error:${C_RESET} $*" >&2; exit 1; }
info() { echo "${C_BLUE}>>${C_RESET} $*"; }
ok() { echo "${C_GREEN}✓${C_RESET} $*"; }
warn() { echo "${C_YELLOW}!${C_RESET} $*"; }

# ─────────── 参数 ───────────
DB="${1:-}"
APPLY=false
for arg in "${@:2}"; do
  case "$arg" in
    --apply) APPLY=true ;;
    -h|--help)
      sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "未知参数: $arg" ;;
  esac
done

[ -n "$DB" ] || die "缺 DB 路径。用法: $0 <db_path> [--apply]"
[ -f "$DB" ] || die "DB 文件不存在: $DB"

# 跨平台 sqlite3 检查
command -v sqlite3 >/dev/null 2>&1 || die "找不到 sqlite3,请先安装"

# 表存在性检查 — 避免对非 BeeCount-Cloud DB 误操作
for tbl in sync_changes user_account_projection user_category_projection \
           user_tag_projection read_tx_projection read_budget_projection; do
  found=$(sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='$tbl';")
  [ -n "$found" ] || die "DB 缺表 $tbl,确定是 BeeCount-Cloud DB 吗?"
done

# ─────────── 前置统计 ───────────
info "目标 DB: ${C_BOLD}$DB${C_RESET}"
info "模式:  $([ "$APPLY" = true ] && echo "${C_RED}${C_BOLD}REAL${C_RESET} (会改 DB)" || echo "${C_YELLOW}${C_BOLD}DRY-RUN${C_RESET} (不改 DB)")"
echo

info "清理前 sync_changes 分布:"
sqlite3 -header -column "$DB" "
SELECT entity_type, action, COUNT(*) AS cnt
FROM sync_changes
GROUP BY entity_type, action
ORDER BY cnt DESC;
"
echo

# 各实体 projection 跟 sync_changes 的对比
info "清理前 projection vs sync_changes 比例:"
sqlite3 -header -column "$DB" "
SELECT 'account' AS t,
  (SELECT COUNT(*) FROM user_account_projection) AS proj,
  (SELECT COUNT(*) FROM sync_changes WHERE entity_type='account' AND action='upsert') AS sc,
  printf('%.2fx', CAST((SELECT COUNT(*) FROM sync_changes WHERE entity_type='account' AND action='upsert') AS REAL)
                / NULLIF((SELECT COUNT(*) FROM user_account_projection), 0)) AS ratio
UNION ALL SELECT 'category',
  (SELECT COUNT(*) FROM user_category_projection),
  (SELECT COUNT(*) FROM sync_changes WHERE entity_type='category' AND action='upsert'),
  printf('%.2fx', CAST((SELECT COUNT(*) FROM sync_changes WHERE entity_type='category' AND action='upsert') AS REAL)
                / NULLIF((SELECT COUNT(*) FROM user_category_projection), 0))
UNION ALL SELECT 'tag',
  (SELECT COUNT(*) FROM user_tag_projection),
  (SELECT COUNT(*) FROM sync_changes WHERE entity_type='tag' AND action='upsert'),
  printf('%.2fx', CAST((SELECT COUNT(*) FROM sync_changes WHERE entity_type='tag' AND action='upsert') AS REAL)
                / NULLIF((SELECT COUNT(*) FROM user_tag_projection), 0))
UNION ALL SELECT 'transaction',
  (SELECT COUNT(*) FROM read_tx_projection),
  (SELECT COUNT(*) FROM sync_changes WHERE entity_type='transaction' AND action='upsert'),
  printf('%.2fx', CAST((SELECT COUNT(*) FROM sync_changes WHERE entity_type='transaction' AND action='upsert') AS REAL)
                / NULLIF((SELECT COUNT(*) FROM read_tx_projection), 0))
UNION ALL SELECT 'budget',
  (SELECT COUNT(*) FROM read_budget_projection),
  (SELECT COUNT(*) FROM sync_changes WHERE entity_type='budget' AND action='upsert'),
  printf('%.2fx', CAST((SELECT COUNT(*) FROM sync_changes WHERE entity_type='budget' AND action='upsert') AS REAL)
                / NULLIF((SELECT COUNT(*) FROM read_budget_projection), 0));
"
echo

# ─────────── dry-run 算出"会删多少" ───────────
info "估算可清理的 row 数(dry-run,任何模式都会跑)..."
DRY=$(sqlite3 "$DB" "
SELECT (
  -- step 1: 已 delete 的 entity 上的 upsert
  SELECT COUNT(*) FROM sync_changes
  WHERE action != 'delete'
    AND (user_id, entity_type, entity_sync_id) IN (
      SELECT DISTINCT user_id, entity_type, entity_sync_id
      FROM sync_changes WHERE action = 'delete'
    )
) AS step1,
(
  -- step 2: 未 delete entity 的非最新 upsert
  SELECT COUNT(*) FROM sync_changes
  WHERE action != 'delete'
    AND change_id NOT IN (
      SELECT MAX(change_id) FROM sync_changes
      WHERE action != 'delete'
      GROUP BY user_id, entity_type, entity_sync_id
    )
    AND (user_id, entity_type, entity_sync_id) NOT IN (
      SELECT DISTINCT user_id, entity_type, entity_sync_id
      FROM sync_changes WHERE action = 'delete'
    )
) AS step2;
")
STEP1=$(echo "$DRY" | cut -d'|' -f1)
STEP2=$(echo "$DRY" | cut -d'|' -f2)
TOTAL=$((STEP1 + STEP2))

echo "  ${C_BOLD}已删 entity 的 upsert (step 1):${C_RESET} $STEP1 条"
echo "  ${C_BOLD}未删 entity 的非最新 upsert (step 2):${C_RESET} $STEP2 条"
echo "  ${C_BOLD}合计可清理:${C_RESET} $TOTAL 条"
echo

if [ "$TOTAL" -eq 0 ]; then
  ok "DB 已经干净,无需清理。"
  exit 0
fi

# ─────────── dry-run 结束 ───────────
if [ "$APPLY" != true ]; then
  warn "DRY-RUN 模式,DB 未改动。"
  warn "要真做,加 ${C_BOLD}--apply${C_RESET} 重跑。"
  exit 0
fi

# ─────────── 真正执行 ───────────
TS=$(date +%Y%m%d-%H%M%S)
BAK="${DB}.bak-before-compact-${TS}"
info "备份 DB → $BAK"
cp "$DB" "$BAK"
ok "备份完成 ($(du -h "$BAK" | cut -f1))"
echo

read -r -p "$(echo -e "${C_YELLOW}${C_BOLD}最后确认${C_RESET}: 即将清理 ${TOTAL} 条 sync_changes row + 修复 projection 引用。输入 yes 继续: ")" CONFIRM
[ "$CONFIRM" = "yes" ] || { warn "用户取消"; exit 1; }
echo

info "执行清理事务..."

# 用 sqlite3 inline + heredoc,失败 exit code != 0 触发 set -e
sqlite3 "$DB" <<'SQL' 2>&1 | tee /tmp/compact_sync_changes.log
BEGIN;

-- step 1
DELETE FROM sync_changes
WHERE (user_id, entity_type, entity_sync_id) IN (
  SELECT DISTINCT user_id, entity_type, entity_sync_id
  FROM sync_changes WHERE action = 'delete'
)
AND action != 'delete';

-- step 2
DELETE FROM sync_changes
WHERE action != 'delete'
  AND change_id NOT IN (
    SELECT MAX(change_id) FROM sync_changes
    WHERE action != 'delete'
    GROUP BY user_id, entity_type, entity_sync_id
  );

-- step 3:修复 projection.source_change_id
UPDATE user_account_projection
SET source_change_id = COALESCE((
  SELECT MAX(sc.change_id) FROM sync_changes sc
  WHERE sc.user_id = user_account_projection.user_id
    AND sc.entity_type = 'account'
    AND sc.entity_sync_id = user_account_projection.sync_id
), source_change_id);

UPDATE user_category_projection
SET source_change_id = COALESCE((
  SELECT MAX(sc.change_id) FROM sync_changes sc
  WHERE sc.user_id = user_category_projection.user_id
    AND sc.entity_type = 'category'
    AND sc.entity_sync_id = user_category_projection.sync_id
), source_change_id);

UPDATE user_tag_projection
SET source_change_id = COALESCE((
  SELECT MAX(sc.change_id) FROM sync_changes sc
  WHERE sc.user_id = user_tag_projection.user_id
    AND sc.entity_type = 'tag'
    AND sc.entity_sync_id = user_tag_projection.sync_id
), source_change_id);

UPDATE read_tx_projection
SET source_change_id = COALESCE((
  SELECT MAX(sc.change_id) FROM sync_changes sc
  WHERE sc.user_id = read_tx_projection.user_id
    AND sc.entity_type = 'transaction'
    AND sc.entity_sync_id = read_tx_projection.sync_id
), source_change_id);

UPDATE read_budget_projection
SET source_change_id = COALESCE((
  SELECT MAX(sc.change_id) FROM sync_changes sc
  WHERE sc.user_id = read_budget_projection.user_id
    AND sc.entity_type = 'budget'
    AND sc.entity_sync_id = read_budget_projection.sync_id
), source_change_id);

COMMIT;
SQL

ok "清理事务执行完成。"
echo

# ─────────── 验证 ───────────
info "Sanity check:projection 是否还有 dangling source_change_id..."
DANGLING=$(sqlite3 "$DB" "
SELECT (
  SELECT COUNT(*) FROM user_account_projection p
  LEFT JOIN sync_changes sc ON sc.change_id = p.source_change_id AND sc.user_id = p.user_id
  WHERE sc.change_id IS NULL
) + (
  SELECT COUNT(*) FROM user_category_projection p
  LEFT JOIN sync_changes sc ON sc.change_id = p.source_change_id AND sc.user_id = p.user_id
  WHERE sc.change_id IS NULL
) + (
  SELECT COUNT(*) FROM user_tag_projection p
  LEFT JOIN sync_changes sc ON sc.change_id = p.source_change_id AND sc.user_id = p.user_id
  WHERE sc.change_id IS NULL
) + (
  SELECT COUNT(*) FROM read_tx_projection p
  LEFT JOIN sync_changes sc ON sc.change_id = p.source_change_id AND sc.user_id = p.user_id
  WHERE sc.change_id IS NULL
) + (
  SELECT COUNT(*) FROM read_budget_projection p
  LEFT JOIN sync_changes sc ON sc.change_id = p.source_change_id AND sc.user_id = p.user_id
  WHERE sc.change_id IS NULL
);
")

if [ "$DANGLING" -ne 0 ]; then
  warn "${C_RED}发现 $DANGLING 条 dangling projection 引用!${C_RESET}"
  warn "DB 已经 commit,无法自动回滚。请用备份恢复:"
  echo "    cp $BAK $DB"
  exit 2
fi
ok "0 dangling projection 引用。"
echo

# ─────────── 终态汇总 ───────────
info "清理后 sync_changes 分布:"
sqlite3 -header -column "$DB" "
SELECT entity_type, action, COUNT(*) AS cnt
FROM sync_changes
GROUP BY entity_type, action
ORDER BY cnt DESC;
"
echo

info "清理后 projection vs sync_changes 比例(应全部 ≈ 1.00x):"
sqlite3 -header -column "$DB" "
SELECT 'account' AS t,
  (SELECT COUNT(*) FROM user_account_projection) AS proj,
  (SELECT COUNT(*) FROM sync_changes WHERE entity_type='account' AND action='upsert') AS sc,
  printf('%.2fx', CAST((SELECT COUNT(*) FROM sync_changes WHERE entity_type='account' AND action='upsert') AS REAL)
                / NULLIF((SELECT COUNT(*) FROM user_account_projection), 0)) AS ratio
UNION ALL SELECT 'category',
  (SELECT COUNT(*) FROM user_category_projection),
  (SELECT COUNT(*) FROM sync_changes WHERE entity_type='category' AND action='upsert'),
  printf('%.2fx', CAST((SELECT COUNT(*) FROM sync_changes WHERE entity_type='category' AND action='upsert') AS REAL)
                / NULLIF((SELECT COUNT(*) FROM user_category_projection), 0))
UNION ALL SELECT 'tag',
  (SELECT COUNT(*) FROM user_tag_projection),
  (SELECT COUNT(*) FROM sync_changes WHERE entity_type='tag' AND action='upsert'),
  printf('%.2fx', CAST((SELECT COUNT(*) FROM sync_changes WHERE entity_type='tag' AND action='upsert') AS REAL)
                / NULLIF((SELECT COUNT(*) FROM user_tag_projection), 0))
UNION ALL SELECT 'transaction',
  (SELECT COUNT(*) FROM read_tx_projection),
  (SELECT COUNT(*) FROM sync_changes WHERE entity_type='transaction' AND action='upsert'),
  printf('%.2fx', CAST((SELECT COUNT(*) FROM sync_changes WHERE entity_type='transaction' AND action='upsert') AS REAL)
                / NULLIF((SELECT COUNT(*) FROM read_tx_projection), 0))
UNION ALL SELECT 'budget',
  (SELECT COUNT(*) FROM read_budget_projection),
  (SELECT COUNT(*) FROM sync_changes WHERE entity_type='budget' AND action='upsert'),
  printf('%.2fx', CAST((SELECT COUNT(*) FROM sync_changes WHERE entity_type='budget' AND action='upsert') AS REAL)
                / NULLIF((SELECT COUNT(*) FROM read_budget_projection), 0));
"
echo

# 总收益统计
SIZE_BEFORE=$(stat -f%z "$BAK" 2>/dev/null || stat -c%s "$BAK")
SIZE_AFTER=$(stat -f%z "$DB" 2>/dev/null || stat -c%s "$DB")
SAVED=$((SIZE_BEFORE - SIZE_AFTER))
SAVED_MB=$(awk -v b="$SAVED" 'BEGIN{printf "%.2f", b/1024/1024}')

# 没 VACUUM 时 DB 文件大小不会缩小(SQLite 不会回收 free pages),提示一下
ok "${C_BOLD}清理完成${C_RESET}"
echo "  清理 row 数:  $TOTAL 条 sync_changes"
echo "  备份位置:    $BAK"
echo "  DB 大小变化:  $((SIZE_BEFORE / 1024 / 1024))MB → $((SIZE_AFTER / 1024 / 1024))MB"
if [ "$SAVED" -le 0 ]; then
  echo
  warn "DB 文件大小没缩小很正常 — SQLite 默认不回收 free pages。"
  echo "    要回收空间,跑(注意会重写整个文件,需要 2x 磁盘空间):"
  echo "    ${C_BOLD}sqlite3 $DB 'VACUUM;'${C_RESET}"
fi
echo
ok "全部完成。备份保留在 $BAK,确认一切正常后可手动删除。"
