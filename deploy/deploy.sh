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
# 可写数据目录：词典与受管 Key（管理页面热写入）。挂载为容器内 /data。
# 用目录挂载（而非单文件），原子写入的临时文件 + os.replace 才能在其中完成。
DATA_DIR="$ROOT/data"
KEYS_FILE="$DATA_DIR/api_keys.txt"
DICT_FILE="$DATA_DIR/sensitive_dict.txt"

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
    echo "DESENTI_API_KEYS_FILE=/data/api_keys.txt"
    echo "DESENTI_DICT_FILE=/data/sensitive_dict.txt"
    echo "DESENTI_SPACY_ENABLED=$([ "$WITH_SPACY" = "1" ] && echo true || echo false)"
    echo "DESENTI_SPACY_MODEL=$SPACY_MODEL"
    echo "DESENTI_LLM_ENABLED=false"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  log "已写入 $ENV_FILE（权限 600）"
  log "生成的 API Key：$API_KEY"
else
  log "复用已有 .env（不覆盖业务配置）。如需改 Key 请手动编辑。"
  # 迁移旧版相对路径 -> /data（早期版本把数据文件挂在 /app，非 root 用户不可写）
  sed -i -E 's#^DESENTI_API_KEYS_FILE=(api_keys\.txt)$#DESENTI_API_KEYS_FILE=/data/api_keys.txt#' "$ENV_FILE"
  sed -i -E 's#^DESENTI_DICT_FILE=(sensitive_dict\.txt)$#DESENTI_DICT_FILE=/data/sensitive_dict.txt#' "$ENV_FILE"
fi

# 管理令牌：幂等处理，无论 .env 是新建还是已存在均适用。
# ENABLE_ADMIN=1 且尚无令牌 -> 生成并追加；已有令牌则不动。
if [ "$ENABLE_ADMIN" = "1" ]; then
  if grep -q "^DESENTI_ADMIN_TOKEN=." "$ENV_FILE"; then
    log "管理令牌已存在于 .env，保持不变。"
  else
    ADMIN_TOKEN="$(gen_secret)"
    # 先删可能存在的空值行，再追加，避免重复键
    sed -i '/^DESENTI_ADMIN_TOKEN=$/d' "$ENV_FILE"
    echo "DESENTI_ADMIN_TOKEN=$ADMIN_TOKEN" >> "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    log "已启用管理页面，生成的管理令牌：$ADMIN_TOKEN"
  fi
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
# 4) 重建容器（从 .env 读取配置；挂载 data 目录以持久化管理页面的改动）
# ----------------------------------------------------------------------------
# 准备可写数据目录：种子词典（沿用仓库内容）+ 受管 Key 文件
mkdir -p "$DATA_DIR"
if [ ! -f "$DICT_FILE" ] && [ -f "$ROOT/sensitive_dict.txt" ]; then
  cp "$ROOT/sensitive_dict.txt" "$DICT_FILE"
fi
touch "$DICT_FILE" "$KEYS_FILE"

# 以宿主调用用户的 uid:gid 运行容器，使其能写挂载进来的 data 目录
# （目录归该用户所有，无需 chown/root；规避非 root 容器用户无写权限的问题）。
RUN_ARGS=(--user "$(id -u):$(id -g)")
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
  -v "$DATA_DIR:/data" \
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
