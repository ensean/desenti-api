# 部署指南

通用部署方法与推荐配置。**不含**具体实例 ID、分发 ID、域名、Key 等环境特定信息——
这些是「某次部署的活值」，会随 stop/start、重建而变化，应记录在实例本地
（`~/desenti-api/.env`，不入库）或私有运维记录中，不要写进仓库。

## 一键部署（Linux 主机）

在主机上克隆/同步本项目后，于项目根目录运行：

```bash
./deploy/deploy.sh                    # 含 spaCy(trf) 的完整 fast 模式
WITH_SPACY=0 ./deploy/deploy.sh       # 轻量：仅字典 + 正则
SPACY_MODEL=zh_core_web_sm ./deploy/deploy.sh   # 轻量 spaCy 模型
ENABLE_ADMIN=1 ./deploy/deploy.sh     # 同时启用 /admin（自动生成管理令牌）
```

脚本是**幂等**的（可重复运行以更新：重建镜像 + 重启容器），会：
- 预检 Docker（缺失时 `INSTALL_DOCKER=1` 可尝试自动安装）；
- 首次运行生成 `.env` 与随机 API Key（权限 600，不入库），并打印 Key；
- 用 build arg 决定是否装入 spaCy 模型；
- 以 `--env-file` 启动容器，挂载 `api_keys.txt` 与 `sensitive_dict.txt`
  到持久路径，使管理页面的改动在容器重建后保留；
- 健康检查后输出访问地址。

可配置环境变量：`IMAGE` `CONTAINER` `PORT` `WITH_SPACY` `SPACY_MODEL`
`ENABLE_ADMIN` `INSTALL_DOCKER` `SECCOMP_UNCONFINED`。

> 暴露到公网仍需在前面加 CloudFront（见下文），脚本只负责把服务在主机上跑起来。

### 推荐主机环境

关键不是 Ubuntu 版本本身，而是 **Docker ≥ 20.10**——它的新 seccomp 配置才放行
现代 glibc 用的 `clone3()`，否则会撞到下方「旧版 Docker」的一堆坑。

| 场景 | 推荐 |
|------|------|
| 通用 / fast 模式 | **Ubuntu 24.04 LTS**（首选）或 22.04 LTS；支持期内、内核与 Docker 都够新 |
| GPU / accurate 模式（AWS） | **AWS Deep Learning AMI（Amazon Linux 2023）**：NVIDIA 驱动 + 新 Docker 预装，开箱即用 |
| 避免 | EOL 系统（16.04/18.04）、非 LTS interim 版 |

装 Docker 建议用官方源拿最新引擎（而非发行版自带的旧 `docker.io`）：

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # 之后免 sudo（重新登录生效）
```

满足上述环境时，直接 `./deploy/deploy.sh` 即可跑完整 spaCy 模式，无需任何兼容开关。

### 旧版 Docker / EOL 系统的主机

现代 OS + Docker 20.10+（推荐，如 AL2023 / Ubuntu 22.04+）可直接用上面的命令，
含 spaCy 的完整 fast 模式开箱即用。

但**旧主机**（如 Ubuntu 16.04 + Docker 18.09）有两个已知坑，均源于过时的
seccomp 配置拦截了现代 glibc 用的 `clone3()` 系统调用：

- 构建时装 spaCy/torch、或运行时创建线程都会报 `can't start new thread`；
- uvloop 在老内核上还可能段错误（exit 139）。

对这类主机：

```bash
WITH_SPACY=0 SECCOMP_UNCONFINED=1 ./deploy/deploy.sh
```

- `SECCOMP_UNCONFINED=1` 给容器加 `--security-opt seccomp=unconfined` 放行 `clone3`，
  一并解决线程与 uvloop 段错误（代价：容器隔离强度降低，仅旧主机使用）。
- `WITH_SPACY=0`：旧版 legacy builder 无法在构建期放行 seccomp，spaCy 模型
  下载会失败，故旧主机只能跑「字典 + 正则」（fast 自动降级，功能正常）。

**建议**：spaCy/LLM 这类 ML 依赖应跑在现代 OS + 较新 Docker 上。EOL 系统
（如 16.04）只适合跑轻量「字典 + 正则」模式。

## 拓扑

```
Internet
  → CloudFront  (边缘 TLS，强制 HTTPS)
  → EC2 origin  (HTTP :8000)
       container: desenti-api  (fast = 字典 + 正则 + spaCy zh_core_web_trf)
```

- 对互联网暴露时用 CloudFront 作为前端，源站不直接对公网开放。
- CPU 实例（如 m7i.xlarge）跑 fast 模式；accurate 模式需 GPU 实例 + 本机 Ollama。

## 安全设计（遵守 steering 规则）

- **绝不开 0.0.0.0/0**。源站 8000 端口仅允许 CloudFront 托管前缀列表
  `com.amazonaws.global.cloudfront.origin-facing` 入站
  （prefix-list ID 因区域而异，用下方命令查询）。
- CloudFront 边缘做 TLS，查看器协议 `redirect-to-https`（HTTP→301）。
- SSH 仅按需放行运维方当前 IP 的 /32，用完可移除。

```bash
# 查询本区域 CloudFront origin-facing 前缀列表 ID
aws ec2 describe-managed-prefix-lists --profile <profile> --region <region> \
  --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
  --query 'PrefixLists[0].PrefixListId' --output text
```

## CloudFront 推荐配置

新建/重建分发时按此配置（控制台或 `aws cloudfront create-distribution` 均可）：

| 项 | 推荐值 | 说明 |
|----|--------|------|
| Origin domain | EC2 公有 DNS 或 Elastic IP | 长期服务建议挂 EIP，避免 IP 变动 |
| Origin protocol | HTTP only，端口 `8000` | TLS 只在边缘做，源站走内网 HTTP |
| Viewer protocol policy | `redirect-to-https` | 强制 HTTPS |
| Allowed methods | `GET,HEAD,OPTIONS,PUT,POST,PATCH,DELETE` | 需含 POST 与 OPTIONS（CORS 预检） |
| Cache policy | `Managed-CachingDisabled` | API 不缓存 |
| Origin request policy | `Managed-AllViewerExceptHostHeader` | 转发 `Authorization`，不转发 Host |
| Origin read timeout | 60s；accurate 走 LLM 时酌情调大 | 慢响应避免被边缘提前断开 |
| Price class | 按覆盖区域选择（如含亚太用 `PriceClass_200`） | 影响成本 |

> 托管策略 ID 可用 `aws cloudfront list-cache-policies --type managed` 与
> `aws cloudfront list-origin-request-policies --type managed` 查询。

## 应用行为

- **CORS 全开**：服务对所有来源返回 `Access-Control-Allow-Origin: *`
  （硬编码，见 `app/main.py`），供浏览器型调用方跨域调用。用 Bearer 头鉴权
  而非 Cookie，`*` 不泄露 Key、不绕过鉴权。
- **accurate 降级**：未配置可用 LLM（`DESENTI_LLM_ENABLED=false` 或后端不可达）时，
  `mode=accurate` 自动降级为 fast，结果与 fast 一致。

## 配置持久化

把 API Key 等配置写入实例本地 `~/desenti-api/.env`（权限 600，**不入库**），
容器以 `--env-file` 启动，重建/重启不丢：

```bash
sudo docker run -d --name desenti-api -p 8000:8000 \
  --add-host host.docker.internal:host-gateway \
  --env-file ~/desenti-api/.env --restart unless-stopped <image>
```

## 部署验证清单

| 检查 | 期望 |
|------|------|
| `GET /health`（HTTPS） | 200 |
| `POST /api/v1/contract/ner`（带 Bearer，fast） | 200，实体正确 |
| 无 Authorization | 401（鉴权生效，未被绕过/缓存） |
| 明文 HTTP | 301 → HTTPS |
| 源站 8000 直连（非 CloudFront） | 超时（仅 CloudFront 可达） |

> spaCy trf 首请求需加载模型（CPU 上约 10s），之后热请求显著加快。

## 运维提醒

- **公网 IP 会变**：实例 stop/start 后公有 IP 与公有 DNS 都会变；若 CloudFront
  源站用的是公有 DNS，需相应更新源站域名。长期服务建议挂 **Elastic IP** 固定地址。
- 实例 ID、分发 ID、域名、Key 等具体值记录在运维侧，不写入本仓库。
