# 合同敏感信息识别 API

基于 FastAPI 的中文合同 NER（命名实体识别）服务。接收合同纯文本，返回敏感实体列表（公司名、人名、地址、电话、银行账号、信用代码等），并标注角色（甲方/乙方/丙方）与语义字段（context_field），用于合同审核流程的自动脱敏。

实现依据：`req.md`。

## 特性

- **双模式识别引擎**
  - `fast`（默认）：**字典 + 正则 + spaCy NER** 级联（参考 [contract-mask-with-nlp](https://github.com/ensean/contract-mask-with-nlp)）。
  - `accurate`：fast + 自托管 LLM（EC2 host），进一步提升召回与角色判定质量。
- **fast 模式优先级级联**（高 → 低，高优先级覆盖重叠的低优先级）

  | 来源 | 负责 |
  |------|------|
  | 词典 `sensitive_dict.txt` | 已知实体强制匹配，可纠正错误切分（如含「银行」的公司全称），热更新 |
  | 字段锚定正则 | `法定代表人：`/`户名：` 等字段后的值 |
  | 强格式正则 + 校验 | 信用代码、身份证、手机/座机、邮箱、银行账号、开户行 |
  | spaCy NER 兜底 | 公司名（ORG）、人名（PERSON）、地址（FAC/LOC） |

- **职责划分（混合而非替换）**
  - 强格式实体始终走正则 + 校验：偏移精确、确定。
  - 公司名/人名/地址：正则兜底 + spaCy 干净跨度覆盖正则的过度捕获；accurate 再叠加 LLM。
  - **LLM 只返回实体值**，字符偏移由引擎在原文回填，重复值依次定位、幻觉值丢弃。
  - 角色/语义字段优先用基于位置的规则判定，规则判不出时回退到 LLM/模型判定。
- **优雅降级（两层）**
  - spaCy 模型未安装/加载失败 → fast 自动降级为「字典 + 正则」。
  - LLM 超时/连接失败/解析失败 → accurate 自动降级为 fast。
  - 响应 `meta.mode` 标明实际使用的模式，请求永不失败。
- **角色判定**：向前 `context_window` 字符匹配最近角色关键词（需求第 4 节）
- **语义字段**：`签约主体 / 法定代表人 / 联系人 / 注册地址 / ...`（需求第 5 节）
- **重叠裁决**：按来源优先级 + 类型优先级 + 跨度 + 置信度去重，结果区间互不重叠
- Bearer Token 认证、内存滑动窗口限流、统一错误码
- **不持久化**请求文本（仅内存处理，处理后丢弃）

> spaCy 与 LLM 均为**可选**：不装 spaCy，fast 退化为字典+正则仍可用；
> 不开 LLM，accurate 退化为 fast。基础镜像保持轻量。
>
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

### 启用 fast 模式的 spaCy NER（可选）

不装 spaCy 时 fast 模式为「字典 + 正则」。装上后会用 spaCy 补充公司名/人名/地址识别：

```bash
./.venv/bin/pip install -r requirements-nlp.txt
# 生产（高精度，~400MB BERT，需 torch）：
./.venv/bin/python -m spacy download zh_core_web_trf
# 或轻量（无需 torch，精度较低，仅用于验证管线）：
./.venv/bin/python -m spacy download zh_core_web_sm
```

通过环境变量选择模型（内网可填本地模型目录路径）：

```bash
export DESENTI_SPACY_MODEL=zh_core_web_trf   # 默认值
```

### Docker

```bash
# 默认镜像（轻量，fast = 字典 + 正则）
docker compose up --build
# 或手动构建
docker build -t desenti-api .
docker run -p 8000:8000 -e DESENTI_API_KEYS=demo-key desenti-api

# 构建时装入 spaCy NER（镜像较大，含 ~400MB 模型）
docker build --build-arg WITH_SPACY=1 --build-arg SPACY_MODEL=zh_core_web_trf -t desenti-api:nlp .
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
| `DESENTI_DICT_FILE` | `sensitive_dict.txt` | 敏感词典路径（最高优先级，热更新） |
| `DESENTI_SPACY_ENABLED` | `true` | 是否启用 spaCy NER 兜底 |
| `DESENTI_SPACY_MODEL` | `zh_core_web_trf` | spaCy 中文模型（或本地模型目录路径） |
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

下表为在东京 g6e.xlarge（NVIDIA L40S）上实测结果。fast 模式启用了 spaCy
`zh_core_web_trf`（首请求加载模型 ~4.4s，之后常驻，**热请求 60-110ms**）；
accurate 模式叠加 `qwen3.5:9b`（100% GPU）。脚本见 `scripts/compare_modes.py`。
格式：`值｜type｜role｜context_field`。

### 1. 自由文本中的公司名 + 人名

> 本协议由智算无界（北京）网络科技合伙企业与乙方签订，经办人为周明远先生。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | `智算无界（北京）网络科技合伙企业`｜company_name｜unknown｜其他<br>`为周明远`｜person_name｜party_b｜联系人 | 76ms |
| accurate | `智算无界（北京）网络科技合伙企业`｜company_name｜**party_a**｜签约主体<br>`为周明远`｜person_name｜party_b｜联系人 | 4957ms |

spaCy 已给出干净公司边界（不含「本协议由」）；accurate 进一步判出甲方角色。

### 2. 无锚点的口语化人名（旧纯规则漏检）

> 项目由王建国负责对接，技术问题可直接联系工程师李娜。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | `王建国`｜person_name｜unknown｜其他<br>`李娜`｜person_name｜unknown｜其他 | 63ms |
| accurate | `王建国`｜person_name｜unknown｜联系人<br>`李娜`｜person_name｜unknown｜联系人 | 913ms |

无锚点人名，spaCy 已能召回（纯规则时此例为空）；accurate 额外补出 context_field。

### 3. 地址夹在句子中（accurate 更完整）

> 乙方将货物运送至承运人位于上海市浦东新区张江高科技园区博云路2号的仓库。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | `浦东新区`｜address｜party_b｜其他<br>`张江高科技园区`｜address｜party_b｜其他 | 72ms |
| accurate | `上海市浦东新区张江高科技园区博云路2号`｜address｜party_b｜经营地址 | 824ms |

spaCy LOC 把地址切成片段；LLM 给出完整门牌级地址。**地址完整性 accurate 明显更好。**

### 4. 多方角色嵌套（甲/乙/丙）

> 甲方海创集团有限公司委托乙方中科软件技术股份有限公司开发，丙方天平律师事务所提供法律见证。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | `甲方海创集团有限公司`｜unknown<br>`中科软件技术股份有限公司`｜party_b<br>`丙方天平律师事务所`｜party_b | 85ms |
| accurate | `海创集团有限公司`｜**party_a**<br>`中科软件技术股份有限公司`｜**party_b**<br>`天平律师事务所`｜**party_c** | 1211ms |

fast 仍有角色前缀粘连与角色错位；LLM 三方边界与角色全部正确。

### 5. 人名 + 职务、无标点（旧纯规则漏检）

> 签约代表张伟出席，监事会成员陈晓红列席本次会议。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | `张伟`｜person_name｜unknown｜其他<br>`陈晓红`｜person_name｜unknown｜其他 | 62ms |
| accurate | `张伟`｜person_name｜unknown｜法定代表人<br>`陈晓红`｜person_name｜unknown｜其他 | 912ms |

spaCy 召回两个人名（纯规则时为空）；accurate 额外判出 context_field。

### 6. 含「银行」的公司全称（词典纠正）

> 深圳前海微众银行股份有限公司（以下简称微众银行）为本协议的资金存管方。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | `深圳前海微众银行股份有限公司`｜company_name｜unknown｜其他<br>`以下简称微众银行`｜bank_name｜unknown｜其他 | 73ms |
| accurate | 同 fast | 774ms |

词典把全称作为单一 `company_name`，纠正了 `bank_name` 正则的切分（详见末节）。

### 7. 强格式实体（两模式一致，由正则负责）

> 开户行：中国建设银行杭州西湖支行，账号6217 0012 3456 7890 123，信用代码91330100MA2H1234XY。

| 模式 | 识别结果 | 耗时 |
|------|----------|------|
| fast | `中国建设银行杭州西湖支行`｜bank_name｜开户行<br>`6217 0012 3456 7890 123`｜bank_account｜银行账号<br>`91330100MA2H1234XY`｜credit_code｜统一社会信用代码 | 108ms |
| accurate | 同 fast（完全一致） | 755ms |

强格式实体始终由正则识别，accurate 不改变结果——印证「LLM/spaCy 只补语义实体、强格式交给正则」的分工。

### 小结

| 维度 | fast（字典+正则+spaCy trf） | accurate（+LLM） |
|------|------|------|
| 延迟（热） | 60-110ms | 约 0.7-5 秒（GPU） |
| 公司名边界 | 干净（spaCy） | 干净 |
| 无锚点人名/公司 | **召回好**（spaCy） | 召回好 |
| 地址完整性 | 易切片段 | **更完整** |
| 角色判定 | 仍依赖关键词位置，自由文本易 unknown/错位 | **语义判定，准** |
| context_field | 依赖锚点 | 更全 |
| 强格式实体 | ✅ 精确 | ✅ 精确（同 fast） |
| 确定性 | 高（spaCy 确定，无采样） | 有模型随机性 |

引入 spaCy 后 **fast 模式召回大幅提升**（口语化人名、自由文本公司名从「漏检」到「召回」），
与 accurate 的差距明显缩小；accurate 仍在**地址完整性**与**角色判定**上更优。
首请求需加载 trf 模型（~4.4s），之后热请求约 60-110ms。

**关于含「银行」的公司全称**：如 `深圳前海微众银行股份有限公司`，`bank_name`
正则会倾向把它切成 `深圳前海微众银行` + `股份有限公司`。通过词典纠正——
把该全称登记到 `sensitive_dict.txt` 的 `[company_name]` 段，词典优先级最高，
会作为单一 `company_name` 实体覆盖正则切分（见 `tests/test_dict_engine.py`）。

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
    engine.py        混合 NER 引擎（编排、级联、重叠裁决、角色/字段、偏移回填）
    dict_engine.py   词典匹配（最高优先级，热更新）
    spacy_backend.py spaCy NER 后端（公司/人名/地址，优雅降级）
    llm_client.py    LLM 客户端（accurate 模式，自托管后端）
    patterns.py      正则与关键词词典
    validators.py    信用代码 / 身份证校验
sensitive_dict.txt 敏感词典（可在线编辑，热更新）
requirements.txt       基础依赖
requirements-nlp.txt   可选 spaCy 依赖
tests/             单元测试 + API 集成测试 + 样例合同
scripts/           演示与对比脚本
Dockerfile, docker-compose.yml
```

## 安全与部署说明

- 服务本身不实现 TLS。生产环境请置于 HTTPS 反向代理 / API 网关之后（TLS 1.2+），满足需求第 8 节传输安全要求。
- 内存限流仅适用于单实例。多实例横向扩展时应替换为 Redis 等共享存储。
- 不持久化合同文本；日志中不记录请求正文。
- API Key 通过环境变量注入，不要硬编码或入库明文。
- **LLM 必须自托管**（EC2 host 上的 Ollama/vLLM）。合同正文绝不发往第三方 API，确保数据不出机器、满足"不持久化"与合规要求。Ollama 默认仅监听 `127.0.0.1`，若 API 与 Ollama 同机部署无需对外开放端口。
