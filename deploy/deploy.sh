#!/usr/bin/env bash
# 一键部署脚本（Linux 主机）。在项目根目录运行：
#     ./deploy/deploy.sh
#
# 幂等：可重复执行以更新（重建镜像 + 重启容器）。
# 配置经环境变量覆盖（见下方默认值）。首次运行会生成 .env 与随机 API Key。
#
# 常用：
#     ./deploy/deploy.sh                      # 含 spaCy(trf) 的完整 fast 模式
#     WITH_SPACY=0 ./deploy/deploy.sh         # 轻量：仅字典+正则
#     SPACY_MODEL=zh_core_web_sm ./deploy/deploy.sh   # 轻量 spaCy 模型
#     ENABLE_ADMIN=1 ./deploy/deploy.sh       # 启用 /admin（自动生成管理令牌）
#     WITH_SPACY=0 SECCOMP_UNCONFINED=1 ./deploy/deploy.sh  # 旧版 Docker 主机
#                                             #   （如 Ubuntu 16.04 / Docker 18.09）：
#                                             #   关 spaCy + 关 seccomp 放行 clone3
set -euo pipefail

# ----------------------------------------------------------------------------
# 配置（环境变量覆盖）
# ----------------------------------------------------------------------------
IMAGE="${IMAGE:-desenti-api:nlp}"
CONTAINER="${CONTAINER:-desenti-api}"
PORT="${PORT:-8000}"
WITH_SPACY="${WITH_SPACY:-1}"               # 1=构建时装入 spaCy；0=仅字典+正则
SPACY_MODEL="${SPACY_MODEL:-zh_core_web_trf}"
ENABLE_ADMIN="${ENABLE_ADMIN:-0}"           # 1=启用 /admin 并生成管理令牌
INSTALL_DOCKER="${INSTALL_DOCKER:-0}"       # 1=缺少 Docker 时尝试自动安装
SECCOMP_UNCONFINED="${SECCOMP_UNCONFINED:-0}"  # 1=容器关 seccomp（旧版 Docker，
                                            #   其老 seccomp 配置会拦截 clone3，
                                            #   导致建镜像/运行时 "can't start new thread"）

# 定位项目根目录（脚本位于 <root>/deploy/）
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ENV_FILE="$ROOT/.env"
KEYS_FILE="$ROOT/api_keys.txt"

log() { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

# ----------------------------------------------------------------------------
# 1) Docker 预检
# ----------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  if [ "$INSTALL_DOCKER" = "1" ]; then
    log "未检测到 Docker，尝试安装…"
    if command -v dnf >/dev/null 2>&1; then sudo dnf install -y docker;
    elif command -v yum >/dev/null 2>&1; then sudo yum install -y docker;
    elif command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y docker.io;
    else err "无法识别包管理器，请手动安装 Docker。"; exit 1; fi
    sudo systemctl enable --now docker || true
  else
    err "未检测到 Docker。请先安装，或用 INSTALL_DOCKER=1 重试。"
    exit 1
  fi
fi

# 是否需要 sudo 调 docker
if docker ps >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo docker"; fi
log "使用 Docker 命令：$DOCKER"

# ----------------------------------------------------------------------------
# 2) 确保 .env（首次生成随机 API Key）
# ----------------------------------------------------------------------------
gen_secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 24;
  else python3 -c "import secrets; print(secrets.token_hex(24))"; fi
}

if [ ! -f "$ENV_FILE" ]; then
  log "未发现 .env，生成默认配置与随机 API Key…"
  API_KEY="key-$(gen_secret)"
  {
    echo "DESENTI_API_KEYS=$API_KEY"
    echo "DESENTI_API_KEYS_FILE=api_keys.txt"
    echo "DESENTI_DICT_FILE=sensitive_dict.txt"
    echo "DESENTI_SPACY_ENABLED=$([ "$WITH_SPACY" = "1" ] && echo true || echo false)"
    echo "DESENTI_SPACY_MODEL=$SPACY_MODEL"
    echo "DESENTI_LLM_ENABLED=false"
  } > "$ENV_FILE"
  if [ "$ENABLE_ADMIN" = "1" ]; then
    ADMIN_TOKEN="$(gen_secret)"
    echo "DESENTI_ADMIN_TOKEN=$ADMIN_TOKEN" >> "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"
  log "已写入 $ENV_FILE（权限 600）"
  log "生成的 API Key：$API_KEY"
  [ "$ENABLE_ADMIN" = "1" ] && log "生成的管理令牌：$ADMIN_TOKEN"
else
  log "复用已有 .env（不覆盖）。如需改 Key/令牌请手动编辑。"
fi

# ----------------------------------------------------------------------------
# 3) 构建镜像
# ----------------------------------------------------------------------------
log "构建镜像 $IMAGE （WITH_SPACY=$WITH_SPACY, SPACY_MODEL=$SPACY_MODEL）…"
$DOCKER build \
  --build-arg "WITH_SPACY=$WITH_SPACY" \
  --build-arg "SPACY_MODEL=$SPACY_MODEL" \
  -t "$IMAGE" .

# ----------------------------------------------------------------------------
# 4) 重建容器（从 .env 读取配置；挂载词典与 Key 文件以持久化管理页面的改动）
# ----------------------------------------------------------------------------
touch "$KEYS_FILE"
DICT_FILE="$ROOT/sensitive_dict.txt"
touch "$DICT_FILE"

# host.docker.internal:host-gateway 需 Docker 20.10+，且仅在容器需访问宿主机
# Ollama（LLM 启用）时有用。LLM 关闭时跳过，避免在旧版 Docker 上 run 失败。
RUN_ARGS=()
if grep -qE '^DESENTI_LLM_ENABLED=true' "$ENV_FILE"; then
  RUN_ARGS+=(--add-host host.docker.internal:host-gateway)
fi
# 旧版 Docker（如 18.09）的 seccomp 配置拦截 clone3，致线程无法创建。
# 关闭容器 seccomp 过滤即可放行（注意：降低了容器隔离强度，仅用于旧主机）。
if [ "$SECCOMP_UNCONFINED" = "1" ]; then
  RUN_ARGS+=(--security-opt seccomp=unconfined)
fi

log "重启容器 $CONTAINER …"
$DOCKER rm -f "$CONTAINER" >/dev/null 2>&1 || true
$DOCKER run -d \
  --name "$CONTAINER" \
  -p "$PORT:8000" \
  ${RUN_ARGS[@]+"${RUN_ARGS[@]}"} \
  --env-file "$ENV_FILE" \
  -v "$KEYS_FILE:/app/api_keys.txt" \
  -v "$DICT_FILE:/app/sensitive_dict.txt" \
  --restart unless-stopped \
  "$IMAGE" >/dev/null

# ----------------------------------------------------------------------------
# 5) 健康检查
# ----------------------------------------------------------------------------
log "等待服务就绪…"
ok=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [ "$ok" = "1" ]; then
  log "✅ 部署完成，健康检查通过：http://127.0.0.1:$PORT/health"
  log "   API Key 见 $ENV_FILE（DESENTI_API_KEYS）"
  [ "$ENABLE_ADMIN" = "1" ] && log "   管理页面：http://127.0.0.1:$PORT/admin"
else
  err "健康检查未通过。查看日志：$DOCKER logs $CONTAINER"
  exit 1
fi
