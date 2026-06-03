# 合同敏感信息识别 API

基于 FastAPI 的中文合同 NER（命名实体识别）服务。接收合同纯文本，返回敏感实体列表（公司名、人名、地址、电话、银行账号、信用代码等），并标注角色（甲方/乙方/丙方）与语义字段（context_field），用于合同审核流程的自动脱敏。

实现依据：`req.md`。

## 特性

- **混合识别引擎**（纯 CPU，无外部模型依赖，开箱即用）
  - 强格式实体用正则 + 校验：统一社会信用代码、身份证、手机/座机、邮箱、银行账号
  - 公司名 / 开户行 / 地址用关键词锚点正则
  - 人名 / 户名用上下文关键词锚点抽取
- **角色判定**：向前 `context_window` 字符匹配最近角色关键词（需求第 4 节）
- **语义字段**：`签约主体 / 法定代表人 / 联系人 / 注册地址 / ...`（需求第 5 节）
- **重叠裁决**：按类型优先级 + 跨度 + 置信度去重，结果区间互不重叠
- Bearer Token 认证、内存滑动窗口限流、统一错误码
- **不持久化**请求文本（仅内存处理，处理后丢弃）

> 引擎设计为可扩展：如需接入 spaCy `zh_core_web_trf` / BERT / 百度 LAC，
> 可在 `app/ner/engine.py` 中合并模型识别结果（保留正则补充强格式实体）。

## 快速开始

### 本地运行

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
DESENTI_API_KEYS=demo-key ./.venv/bin/uvicorn app.main:app --port 8000
```

打开交互式文档：http://127.0.0.1:8000/docs

### Docker

```bash
docker compose up --build
# 或
docker build -t desenti-api .
docker run -p 8000:8000 -e DESENTI_API_KEYS=demo-key desenti-api
```

## 调用示例

```bash
curl -X POST http://127.0.0.1:8000/api/v1/contract/ner \
  -H "Authorization: Bearer demo-key" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "甲方：深圳市星幻科技有限公司\n法定代表人：张三\n电话：13800138000",
    "options": {"min_confidence": 0.7, "context_window": 100}
  }'
```

Python 演示脚本（需先启动服务）：

```bash
DESENTI_API_KEYS=demo-key ./.venv/bin/uvicorn app.main:app --port 8077 &
./.venv/bin/python scripts/demo_request.py
```

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `POST` | `/api/v1/contract/ner` | 合同实体识别（需认证） |

请求/响应/错误码格式见 `req.md` 第 2、6 节。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `DESENTI_API_KEYS` | `dev-local-key` | 合法 API Key，逗号分隔 |
| `DESENTI_MODEL_VERSION` | `1.0.0` | 响应 meta 中的模型版本 |
| `DESENTI_MAX_TEXT_BYTES` | `102400` | 文本上限（UTF-8 字节，100KB） |
| `DESENTI_RATE_LIMIT_PER_MINUTE` | `600` | 每 Key 每分钟请求上限（≥10 QPS） |

复制 `.env.example` 为 `.env` 进行本地配置。**切勿提交真实密钥。**

## 测试

```bash
./.venv/bin/python -m pytest
```

## 项目结构

```
app/
  main.py          FastAPI 应用、路由、认证依赖、统一错误处理
  config.py        环境变量配置
  schemas.py       Pydantic 请求/响应模型
  auth.py          Bearer 认证 + 内存限流
  errors.py        错误码与异常
  ner/
    engine.py      混合 NER 引擎（编排、重叠裁决、角色/字段判定）
    patterns.py    正则与关键词词典
    validators.py  信用代码 / 身份证校验
tests/             单元测试 + API 集成测试 + 样例合同
scripts/           演示脚本
Dockerfile, docker-compose.yml
```

## 安全与部署说明

- 服务本身不实现 TLS。生产环境请置于 HTTPS 反向代理 / API 网关之后（TLS 1.2+），满足需求第 8 节传输安全要求。
- 内存限流仅适用于单实例。多实例横向扩展时应替换为 Redis 等共享存储。
- 不持久化合同文本；日志中不记录请求正文。
- API Key 通过环境变量注入，不要硬编码或入库明文。
