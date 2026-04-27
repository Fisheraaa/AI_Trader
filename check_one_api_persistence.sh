#!/usr/bin/env bash
set -euo pipefail

# ==========================
# one-api 持久化自检脚本
# 用法：
#   bash check_one_api_persistence.sh
# 可选环境变量：
#   CONTAINER_NAME=one-api
#   EXPECT_HOST_PATH=/mnt/d/AI_Trader/one-api-data
# ==========================

CONTAINER_NAME="${CONTAINER_NAME:-one-api}"
EXPECT_HOST_PATH="${EXPECT_HOST_PATH:-/mnt/d/AI_Trader/one-api-data}"

echo "==================== one-api 持久化自检开始 ===================="
date
echo "Container: ${CONTAINER_NAME}"
echo "Expected host path: ${EXPECT_HOST_PATH}"
echo

# ---------- helper ----------
ok()   { echo -e "✅ $*"; }
warn() { echo -e "⚠️  $*"; }
err()  { echo -e "❌ $*"; }

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "缺少命令: $1"
    exit 1
  fi
}

need_cmd docker
need_cmd grep
need_cmd awk
need_cmd sed
need_cmd ls
need_cmd stat
need_cmd date

# ---------- Docker daemon ----------
if docker info >/dev/null 2>&1; then
  ok "Docker daemon 可访问"
else
  err "Docker daemon 不可访问。请先启动 Docker Desktop / Docker 服务。"
  exit 1
fi

# ---------- Container existence ----------
if docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  ok "找到容器 ${CONTAINER_NAME}"
else
  err "未找到容器 ${CONTAINER_NAME}。请先 docker compose up -d"
  exit 1
fi

RUNNING="$(docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}" || true)"
if [[ "${RUNNING}" == "true" ]]; then
  ok "容器正在运行"
else
  warn "容器未运行（仍可检查挂载配置）"
fi

echo
echo "---- [1] 宿主机路径检查 ----"
if [[ -e "${EXPECT_HOST_PATH}" ]]; then
  ok "宿主机路径存在: ${EXPECT_HOST_PATH}"
  ls -la "${EXPECT_HOST_PATH}" || true
else
  warn "宿主机路径不存在，将尝试创建：${EXPECT_HOST_PATH}"
  mkdir -p "${EXPECT_HOST_PATH}" || {
    err "无法创建宿主机路径，请检查权限。"
    exit 1
  }
  ok "已创建宿主机路径"
fi

# 记录宿主机目录mtime
HOST_MTIME_BEFORE="$(stat -c '%y' "${EXPECT_HOST_PATH}" 2>/dev/null || true)"
echo "宿主机目录修改时间(前): ${HOST_MTIME_BEFORE}"

echo
echo "---- [2] 容器挂载检查（最关键）----"
MOUNT_JSON="$(docker inspect -f '{{json .Mounts}}' "${CONTAINER_NAME}")"
echo "Mounts: ${MOUNT_JSON}"

HAS_DATA_MOUNT="false"
DATA_SRC=""
DATA_DEST=""
DATA_TYPE=""

# 尝试提取 destination=/data 的 mount
# 用 grep/sed 简易解析，避免依赖 jq
LINE="$(echo "${MOUNT_JSON}" | tr '}' '\n' | grep '"/data"' || true)"
if [[ -n "${LINE}" ]]; then
  HAS_DATA_MOUNT="true"
  DATA_SRC="$(echo "${LINE}" | sed -n 's/.*"Source":"\([^"]*\)".*/\1/p')"
  DATA_DEST="$(echo "${LINE}" | sed -n 's/.*"Destination":"\([^"]*\)".*/\1/p')"
  DATA_TYPE="$(echo "${LINE}" | sed -n 's/.*"Type":"\([^"]*\)".*/\1/p')"
fi

if [[ "${HAS_DATA_MOUNT}" == "true" ]]; then
  ok "发现容器挂载到 /data"
  echo "Type=${DATA_TYPE} Source=${DATA_SRC} Destination=${DATA_DEST}"

  if [[ "${DATA_SRC}" == "${EXPECT_HOST_PATH}" ]]; then
    ok "Source 与预期一致"
  else
    warn "Source 与预期不一致！"
    echo "  预期: ${EXPECT_HOST_PATH}"
    echo "  实际: ${DATA_SRC}"
  fi
else
  err "未发现 /data 挂载。one-api 数据无法持久化（重建容器会丢）"
fi

echo
echo "---- [3] 容器内 /data 可写性与文件变化检查 ----"
if [[ "${RUNNING}" == "true" ]]; then
  # 列出 /data
  docker exec "${CONTAINER_NAME}" sh -lc 'echo "[container:/data]"; ls -la /data || true'

  # 写一个自检探针文件
  PROBE="persist_probe_$(date +%s).txt"
  docker exec "${CONTAINER_NAME}" sh -lc "echo 'probe at $(date)' > /data/${PROBE}" || {
    err "容器内无法写入 /data（权限或挂载异常）"
    exit 1
  }
  ok "容器内写入 /data/${PROBE} 成功"

  # 检查宿主机是否出现同名文件
  if [[ -f "${EXPECT_HOST_PATH}/${PROBE}" ]]; then
    ok "宿主机可见探针文件 -> 挂载读写链路正常"
  else
    warn "宿主机未看到探针文件，说明你检查的路径可能不是实际挂载源"
    if [[ -n "${DATA_SRC}" && -d "${DATA_SRC}" ]]; then
      warn "尝试在实际 Source 路径查找: ${DATA_SRC}"
      if [[ -f "${DATA_SRC}/${PROBE}" ]]; then
        ok "在实际 Source 找到探针文件。你原先 EXPECT_HOST_PATH 配错了。"
      fi
    fi
  fi
else
  warn "容器未运行，跳过容器内写入测试"
fi

echo
echo "---- [4] one-api 运行日志关键字检查 ----"
echo "最近 120 行日志（筛选关键词）:"
docker logs --tail 120 "${CONTAINER_NAME}" 2>&1 | \
  grep -E "error|fail|sqlite|database|token|invalid|401|permission|panic|migrate|data" -i || true

echo
echo "---- [5] docker-compose 配置文件检查（如果存在）----"
if [[ -f docker-compose.yml ]]; then
  ok "检测到当前目录 docker-compose.yml"
  echo "one-api 段关键行："
  awk '
    BEGIN{in_service=0}
    /^services:/ {print; next}
    /^[[:space:]]*one-api:/ {in_service=1; print; next}
    in_service==1 {
      if ($0 ~ /^[[:space:]]*[a-zA-Z0-9_-]+:/ && $0 !~ /^[[:space:]]*ports:/ && $0 !~ /^[[:space:]]*volumes:/ && $0 !~ /^[[:space:]]*image:/ && $0 !~ /^[[:space:]]*container_name:/ && $0 !~ /^[[:space:]]*restart:/) {
        # 碰到同级新字段也继续打印几行即可
      }
      print
      # 遇到下一个服务（顶格两个空格+名称:）就结束
      if ($0 ~ /^[[:space:]]{2}[a-zA-Z0-9_-]+:[[:space:]]*$/ && $0 !~ /^[[:space:]]*one-api:/) {
        exit
      }
    }
  ' docker-compose.yml | sed -n '1,80p'
else
  warn "当前目录未找到 docker-compose.yml（跳过配置文本检查）"
fi

echo
echo "---- [6] 结论建议模板 ----"
if [[ "${HAS_DATA_MOUNT}" != "true" ]]; then
  err "结论：/data 未挂载。请立即修复 compose：- <host_path>:/data"
elif [[ "${RUNNING}" != "true" ]]; then
  warn "结论：容器未运行。先 docker compose up -d 后重测。"
else
  ok "结论：挂载存在且可写（若 probe 成功）。下一步重点看 one-api 日志与 token/channel 配置。"
fi

echo
echo "==================== 自检结束 ===================="
echo "请把以上完整输出发给我，我会直接给你定点修复方案。"