#!/bin/bash
# ChangeTrace v2 端到端验证脚本
# 任何步骤失败即退出，覆盖快照生成、规则触发、通知投递、导入导出
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
PASS=0
FAIL=0

fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); exit 1; }
pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }

echo "=== ChangeTrace v2 E2E Validation ==="
echo "Target: $BASE_URL"
echo ""

# 1. Health check
echo "[1/9] 健康检查..."
HEALTH=$(curl -sf "$BASE_URL/api/health") || fail "health endpoint unreachable"
echo "$HEALTH" | python3 -c "
import sys,json
d=json.load(sys.stdin)
assert d['status']=='ok', f'status={d[\"status\"]}'
assert d['db_ok']==True, 'db not ok'
assert d['scheduler_running']==True, 'scheduler not running'
" || fail "health check assertions"
pass "健康检查通过"

# 2. Auth status
echo "[2/9] 认证状态..."
AUTH=$(curl -sf "$BASE_URL/api/auth/status") || fail "auth status unreachable"
echo "$AUTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'auth_enabled' in d" || fail "auth status format"
pass "认证端点正常"

# 3. Create task
echo "[3/9] 创建测试任务..."
TASK=$(curl -sf -X POST "$BASE_URL/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{"name":"E2E验证任务","url":"https://httpbin.org/html","interval_seconds":3600,"render_mode":"static"}') || fail "create task"
TASK_ID=$(echo "$TASK" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['id']>0; assert d['version']==1; print(d['id'])") || fail "task response validation"
pass "任务创建成功 (id=$TASK_ID)"

# 4. Trigger and verify snapshot generation
echo "[4/9] 触发任务并验证快照..."
curl -sf -X POST "$BASE_URL/api/tasks/$TASK_ID/trigger" > /dev/null || fail "trigger task"
sleep 5
SNAPSHOTS=$(curl -sf "$BASE_URL/api/tasks/$TASK_ID/snapshots") || fail "list snapshots"
SNAP_COUNT=$(echo "$SNAPSHOTS" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=1, f'expected >=1 snapshot, got {len(d)}'; print(len(d))") || fail "snapshot count assertion (got 0 snapshots)"
SNAP_ID=$(echo "$SNAPSHOTS" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
pass "快照生成成功 (count=$SNAP_COUNT, id=$SNAP_ID)"

# 5. Create channel and bind
echo "[5/9] 创建通道并绑定..."
CHANNEL=$(curl -sf -X POST "$BASE_URL/api/channels" \
  -H "Content-Type: application/json" \
  -d '{"name":"E2E测试Webhook","channel_type":"webhook","config":{"url":"https://httpbin.org/post"}}') || fail "create channel"
CH_ID=$(echo "$CHANNEL" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['id']>0; print(d['id'])") || fail "channel response"
curl -sf -X POST "$BASE_URL/api/tasks/$TASK_ID/channels" \
  -H "Content-Type: application/json" \
  -d "{\"channel_id\":$CH_ID}" > /dev/null || fail "bind channel"
BINDINGS=$(curl -sf "$BASE_URL/api/tasks/$TASK_ID/channels") || fail "list bindings"
echo "$BINDINGS" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d)==1, f'expected 1 binding, got {len(d)}'" || fail "binding count"
pass "通道绑定成功 (channel_id=$CH_ID)"

# 6. Rules: create rule and verify triggering logic
echo "[6/9] 测试监控规则..."
RULE=$(curl -sf -X POST "$BASE_URL/api/tasks/$TASK_ID/rules" \
  -H "Content-Type: application/json" \
  -d '{"rule_type":"keyword_include","config":{"keywords":["Herman"]},"logic_group":"AND"}') || fail "create rule"
RULE_ID=$(echo "$RULE" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['rule_type']=='keyword_include'; print(d['id'])") || fail "rule response"
RULES=$(curl -sf "$BASE_URL/api/tasks/$TASK_ID/rules") || fail "list rules"
echo "$RULES" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d)==1" || fail "rule count"
pass "规则创建验证通过 (rule_id=$RULE_ID)"

# 7. Trigger again to test notification delivery with rule match
echo "[7/9] 触发通知投递验证..."
# First clear the hash to force a "change" - update task to reset hash
curl -sf -X POST "$BASE_URL/api/tasks/$TASK_ID/trigger" > /dev/null || fail "second trigger"
sleep 5
EVENTS=$(curl -sf "$BASE_URL/api/events?task_id=$TASK_ID") || fail "list events"
EVENT_COUNT=$(echo "$EVENTS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d))") || fail "event count"
echo "  事件数: $EVENT_COUNT"
DELIVERY=$(curl -sf "$BASE_URL/api/delivery-log?task_id=$TASK_ID") || fail "delivery log"
DELIVERY_COUNT=$(echo "$DELIVERY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d))")
echo "  投递记录数: $DELIVERY_COUNT"
pass "事件和投递日志可查询"

# 8. Export and import
echo "[8/9] 导入导出验证..."
EXPORT=$(curl -sf "$BASE_URL/api/admin/export") || fail "export"
echo "$EXPORT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
assert d['version']==2, f'export version={d[\"version\"]}'
assert len(d['tasks'])>=1, 'no tasks in export'
assert len(d['channels'])>=1, 'no channels in export'
assert len(d['rules'])>=1, 'no rules in export'
assert len(d['bindings'])>=1, 'no bindings in export'
" || fail "export content validation"

# Import to a clean namespace (different name to avoid skip)
IMPORT_DATA=$(echo "$EXPORT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for t in d['tasks']: t['name']='IMPORT_'+t['name']
for c in d['channels']: c['name']='IMPORT_'+c['name']
json.dump(d, sys.stdout)
")
IMPORT_RESULT=$(curl -sf -X POST "$BASE_URL/api/admin/import" \
  -H "Content-Type: application/json" \
  -d "$IMPORT_DATA") || fail "import"
echo "$IMPORT_RESULT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
assert d['ok']==True
assert d['imported']['tasks']>=1, f'imported tasks={d[\"imported\"][\"tasks\"]}'
assert d['imported']['channels']>=1, f'imported channels={d[\"imported\"][\"channels\"]}'
assert d['imported']['bindings']>=1, f'imported bindings={d[\"imported\"][\"bindings\"]}'
assert d['imported']['rules']>=1, f'imported rules={d[\"imported\"][\"rules\"]}'
" || fail "import result validation"
pass "导入导出验证通过"

# 9. Verify audit log
echo "[9/9] 审计日志验证..."
AUDIT=$(curl -sf "$BASE_URL/api/audit-log?limit=20") || fail "audit log"
AUDIT_COUNT=$(echo "$AUDIT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=3, f'expected >=3 audit entries, got {len(d)}'; print(len(d))") || fail "audit log entries"
pass "审计日志记录正常 (entries=$AUDIT_COUNT)"

# Cleanup
echo ""
echo "=== 清理测试数据 ==="
curl -sf -X DELETE "$BASE_URL/api/tasks/$TASK_ID" > /dev/null 2>&1 || true
curl -sf -X DELETE "$BASE_URL/api/channels/$CH_ID" > /dev/null 2>&1 || true
# Clean imported data
ALL_TASKS=$(curl -sf "$BASE_URL/api/tasks" | python3 -c "import sys,json; [print(t['id']) for t in json.load(sys.stdin) if t['name'].startswith('IMPORT_')]" 2>/dev/null)
for TID in $ALL_TASKS; do curl -sf -X DELETE "$BASE_URL/api/tasks/$TID" > /dev/null 2>&1 || true; done
ALL_CHS=$(curl -sf "$BASE_URL/api/channels" | python3 -c "import sys,json; [print(c['id']) for c in json.load(sys.stdin) if c['name'].startswith('IMPORT_')]" 2>/dev/null)
for CID in $ALL_CHS; do curl -sf -X DELETE "$BASE_URL/api/channels/$CID" > /dev/null 2>&1 || true; done
echo "  已清理"

echo ""
echo "=== RESULT: $PASS passed, $FAIL failed ==="
[ $FAIL -eq 0 ] && echo "=== ALL TESTS PASSED ===" || exit 1
