# 合同敏感信息识别 API

基于 FastAPI 的中文合同 NER（命名实体识别）服务。接收合同纯文本，返回敏感实体列表（公司名、人名、地址、电话、银行账号、信用代码等），并标注角色（甲方/乙方/丙方）与语义字段（context_field），用于合同审核流程的自动脱敏。

实现依据：`req.md`。

## 特性

- **双模式识别引擎**
  - `fast`（默认）：纯规则，毫秒级、确定、零模型依赖。
  - `accurate`：规则 + 自托管 LLM（EC2 host），提升公司名/人名/地址召回与角色判定质量。
- **职责划分（混合而非替换）**
  - 强格式实体（统一社会信用代码、身份证、手机/座机、邮箱、银行账号）始终走正则 + 校验：偏移精确、确定，LLM 在此反而更差。
  - 语义实体（公司名、人名、地址）：fast 用关键词锚点正则；accurate 额外用 LLM。
  - **LLM 只返回实体值**，字符偏移由引擎在原文回填（解决 LLM 数不准字符位置的硬伤），重复值依次定位、幻觉值丢弃。
  - 角色/语义字段优先用基于位置的规则判定，规则判不出时回退到 LLM 的判定。
- **优雅降级**：LLM 超时/连接失败/解析失败时自动降级为纯规则，请求不失败；响应 `meta.mode` 标明实际使用的模式。
- **角色判定**：向前 `context_window` 字符匹配最近角色关键词（需求第 4 节）
- **语义字段**：`签约主体 / 法定代表人 / 联系人 / 注册地址 / ...`（需求第 5 节）
- **重叠裁决**：按类型优先级 + 来源 + 跨度 + 置信度去重，结果区间互不重叠
- Bearer Token 认证、内存滑动窗口限流、统一错误码
- **不持久化**请求文本（仅内存处理，处理后丢弃）

> 合同为敏感数据：LLM 必须自托管（EC2 host 上的 Ollama / vLLM），
> 正文不得发往任何第三方 API。默认模型 `qwen3.5:9b`（官方 Ollama 库，
> 256K 上下文、中文强），思考模式默认关闭以保证 JSON 输出稳定。

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
| `DESENTI_DEFAULT_MODE` | `fast` | 默认识别模式 |
| `DESENTI_LLM_ENABLED` | `false` | 是否启用 LLM 后端 |
| `DESENTI_LLM_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI 兼容地址（Ollama/vLLM） |
| `DESENTI_LLM_MODEL` | `qwen3.5:9b` | 自托管模型名（256K 上下文） |
| `DESENTI_LLM_TIMEOUT_SECONDS` | `120` | LLM 单次请求超时 |
| `DESENTI_LLM_DISABLE_THINKING` | `true` | 关闭思考模式（抽取任务推荐） |

复制 `.env.example` 为 `.env` 进行本地配置。**切勿提交真实密钥。**

## accurate 模式（规则 + 自托管 LLM）

合同是法务敏感数据，**LLM 必须自托管**（不能调第三方 API）。推荐在 EC2 host 上用 Ollama 跑 **Qwen3.5-9B**（官方库、256K 上下文、中文强；100KB 合同可一次喂入无需切块）。纯中文场景的备选是 GLM-4-9B（`glm4:9b`）。

在 EC2 host 上：

```bash
# 安装并启动 Ollama
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3.5:9b
# Ollama 默认监听 127.0.0.1:11434
```

让 API 启用 accurate 后端：

```bash
export DESENTI_LLM_ENABLED=true
export DESENTI_LLM_BASE_URL=http://127.0.0.1:11434/v1   # 容器内用 host.docker.internal
export DESENTI_LLM_MODEL=qwen3.5:9b
```

请求时按需选择模式：

```json
{ "text": "...", "options": { "mode": "accurate" } }
```

- `mode` 缺省为 `fast`。
- 响应 `meta.mode` 表示**实际**使用的模式：请求 `accurate` 但 LLM 不可用时会降级为 `fast`。
- LLM 仅扩充公司名/人名/地址的召回与角色判定；强格式实体始终由正则负责。

> 注意：9B 模型生成较慢，accurate 模式延迟取决于文本长度与 EC2 实例算力。
> 实测 g6e.xlarge（L40S）上小合同约 2-3 秒。**关键**：Ollama 后端必须用原生
> `/api/chat`（`DESENTI_LLM_API_STYLE=ollama`，默认值），因为 Qwen3.5 在 Ollama 的
> OpenAI 兼容端点上不遵守关闭思考的参数，会生成数千推理 token，使单次延迟从
> ~3s 飙到 ~60s。vLLM 后端用 `openai` 风格。

## fast vs accurate 识别效果对比

下表为在东京 g6e.xlarge（NVIDIA L40S）上实测结果，模型 `qwen3.5:9b`（100% GPU）。
脚本见 `scripts/compare_modes.py`。格式：`值｜type｜role｜context_field`。

### 1. 自由文本中的公司名 + 人名

> 本协议由智算无界（北京）网络科技合伙企业与乙方签订，经办人为周明远先生。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | `本协议由智算无界（北京）网络科技合伙企业`｜company_name｜unknown｜其他<br>`为周明远`｜person_name｜party_b｜联系人 | 4ms |
| accurate | `智算无界（北京）网络科技合伙企业`｜company_name｜**party_a**｜签约主体<br>`周明远`｜person_name｜party_b｜联系人 | 4857ms |

规则把「本协议由」「为」这类连接词一并吞入；LLM 给出干净边界并判出甲方。

### 2. 无锚点的口语化人名（规则漏检）

> 项目由王建国负责对接，技术问题可直接联系工程师李娜。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | （无） | 1ms |
| accurate | `王建国`｜person_name｜unknown｜联系人<br>`李娜`｜person_name｜unknown｜联系人 | 843ms |

没有「联系人：」这类锚点时，规则完全漏检；LLM 靠语义识别。

### 3. 地址夹在句子中（规则过度捕获）

> 乙方将货物运送至承运人位于上海市浦东新区张江高科技园区博云路2号的仓库。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | `乙方将货物运送至承运人位于上海市浦东新区张江高科技园区博云路2号的仓库`｜address｜unknown｜其他 | 1ms |
| accurate | `上海市浦东新区张江高科技园区博云路2号`｜address｜party_b｜经营地址 | 746ms |

### 4. 多方角色嵌套（甲/乙/丙）

> 甲方海创集团有限公司委托乙方中科软件技术股份有限公司开发，丙方天平律师事务所提供法律见证。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | `甲方海创集团有限公司`｜unknown<br>`委托乙方中科软件技术股份有限公司`｜party_a<br>`丙方天平律师事务所`｜party_b | 1ms |
| accurate | `海创集团有限公司`｜**party_a**<br>`中科软件技术股份有限公司`｜**party_b**<br>`天平律师事务所`｜**party_c** | 1123ms |

规则把角色前缀粘进实体、且 role 错位；LLM 三方边界与角色全部正确。

### 5. 人名 + 职务、无标点（规则漏检）

> 签约代表张伟出席，监事会成员陈晓红列席本次会议。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | （无） | 1ms |
| accurate | `张伟`｜person_name｜unknown｜法定代表人<br>`陈晓红`｜person_name｜unknown｜其他 | 831ms |

### 6. 强格式实体（两模式一致，由正则负责）

> 开户行：中国建设银行杭州西湖支行，账号6217 0012 3456 7890 123，信用代码91330100MA2H1234XY。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | `中国建设银行杭州西湖支行`｜bank_name｜开户行<br>`6217 0012 3456 7890 123`｜bank_account｜银行账号<br>`91330100MA2H1234XY`｜credit_code｜统一社会信用代码 | 1ms |
| accurate | 同 fast（完全一致） | 675ms |

开户行、账号、信用代码等强格式实体始终由正则识别，accurate 模式不改变结果——印证了「LLM 只补语义实体、强格式交给正则」的分工。

### 小结

| 维度 | fast | accurate |
|------|------|----------|
| 延迟 | 亚毫秒~数毫秒 | 约 0.7-5 秒（GPU） |
| 自由文本边界 | 易过度捕获前后缀 | 干净 |
| 无锚点人名/公司 | 易漏检 | 召回高 |
| 角色判定 | 依赖关键词位置，易错位 | 语义判定，准 |
| 强格式实体 | ✅ 精确 | ✅ 精确（同 fast） |
| 确定性 | 完全确定 | 有模型随机性 |

**已知限制**：当公司全称内部含「银行」时（如 `深圳前海微众银行股份有限公司`），
`bank_name` 正则优先级高于 `company_name`，会把全称切成 `深圳前海微众银行` +
`股份有限公司` 两段，accurate 模式当前也未纠正。这是规则优先级的边界情况，
后续可通过「LLM 给出的更长公司名跨度覆盖内部 bank_name 片段」来修复。

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
    engine.py      混合 NER 引擎（编排、双模式、重叠裁决、角色/字段判定、偏移回填）
    llm_client.py  OpenAI 兼容 LLM 客户端（accurate 模式，自托管后端）
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
- **LLM 必须自托管**（EC2 host 上的 Ollama/vLLM）。合同正文绝不发往第三方 API，确保数据不出机器、满足"不持久化"与合规要求。Ollama 默认仅监听 `127.0.0.1`，若 API 与 Ollama 同机部署无需对外开放端口。
