#!/bin/bash
# ChangeTrace v2 端到端验证脚本
set -e

BASE_URL="${1:-http://localhost:8000}"
echo "=== ChangeTrace v2 E2E Validation ==="
echo "Target: $BASE_URL"
echo ""

# 1. Health check
echo "[1/7] 健康检查..."
HEALTH=$(curl -sf "$BASE_URL/api/health")
echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok', 'Health not ok'; print('  OK: status=ok, db='+str(d['db_ok'])+', scheduler='+str(d['scheduler_running']))"

# 2. Auth status
echo "[2/7] 认证状态..."
AUTH=$(curl -sf "$BASE_URL/api/auth/status")
echo "  $AUTH"

# 3. Create a task
echo "[3/7] 创建测试任务..."
TASK=$(curl -sf -X POST "$BASE_URL/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{"name":"E2E测试任务","url":"https://httpbin.org/html","interval_seconds":3600,"render_mode":"static"}')
TASK_ID=$(echo "$TASK" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  OK: task_id=$TASK_ID"

# 4. Create notification channel
echo "[4/7] 创建通知通道..."
CHANNEL=$(curl -sf -X POST "$BASE_URL/api/channels" \
  -H "Content-Type: application/json" \
  -d '{"name":"测试Webhook","channel_type":"webhook","config":{"url":"https://httpbin.org/post"}}')
CH_ID=$(echo "$CHANNEL" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  OK: channel_id=$CH_ID"

# 5. Bind channel to task
echo "[5/7] 绑定通道到任务..."
curl -sf -X POST "$BASE_URL/api/tasks/$TASK_ID/channels" \
  -H "Content-Type: application/json" \
  -d "{\"channel_id\":$CH_ID}" > /dev/null
echo "  OK"

# 6. Trigger task
echo "[6/7] 触发任务执行..."
curl -sf -X POST "$BASE_URL/api/tasks/$TASK_ID/trigger" > /dev/null
sleep 3
SNAPSHOTS=$(curl -sf "$BASE_URL/api/tasks/$TASK_ID/snapshots")
SNAP_COUNT=$(echo "$SNAPSHOTS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
echo "  OK: snapshots=$SNAP_COUNT"

# 7. Test import/export
echo "[7/7] 测试导入导出..."
EXPORT=$(curl -sf "$BASE_URL/api/admin/export")
echo "$EXPORT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['version']==2; print('  OK: export contains '+str(len(d['tasks']))+' tasks')"

# Cleanup
echo ""
echo "=== 清理测试数据 ==="
curl -sf -X DELETE "$BASE_URL/api/tasks/$TASK_ID" > /dev/null
curl -sf -X DELETE "$BASE_URL/api/channels/$CH_ID" > /dev/null
echo "  已清理"

echo ""
echo "=== ALL TESTS PASSED ==="
