````markdown
# 合同敏感信息识别 API — 需求规格

## 1. 概述

提供一个 REST API 服务，接收中文合同文本，返回识别到的敏感实体列表。用于合同法务审核流程中的自动脱敏环节。

---

## 2. 接口定义

### 2.1 请求

```
POST /api/v1/contract/ner
Content-Type: application/json
Authorization: Bearer <API_KEY>
```

**请求体：**

```json
{
  "text": "合同全文文本（纯文本，无格式）",
  "options": {
    "entity_types": ["all"],
    "min_confidence": 0.7,
    "context_window": 100
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `text` | string | ✅ | 合同全文纯文本，最大 100KB |
| `options.entity_types` | string[] | ❌ | 需要识别的实体类型，默认 `["all"]` |
| `options.min_confidence` | float | ❌ | 最低置信度阈值（0-1），默认 0.7 |
| `options.context_window` | int | ❌ | 上下文窗口字符数（用于角色判定），默认 100 |

### 2.2 响应

```json
{
  "success": true,
  "data": {
    "entities": [
      {
        "value": "深圳市星幻科技有限公司",
        "type": "company_name",
        "role": "party_a",
        "start": 45,
        "end": 56,
        "confidence": 0.95,
        "context_field": "签约主体"
      },
      {
        "value": "张三",
        "type": "person_name",
        "role": "party_a",
        "start": 120,
        "end": 122,
        "confidence": 0.88,
        "context_field": "法定代表人"
      },
      {
        "value": "91440300MA5EXXXXXX",
        "type": "credit_code",
        "role": "party_b",
        "start": 230,
        "end": 248,
        "confidence": 0.99,
        "context_field": "统一社会信用代码"
      }
    ],
    "statistics": {
      "total_entities": 15,
      "by_type": {
        "company_name": 3,
        "person_name": 4,
        "address": 4,
        "phone": 2,
        "bank_account": 1,
        "credit_code": 1
      }
    }
  },
  "meta": {
    "model_version": "1.0.0",
    "processing_time_ms": 320
  }
}
```

---

## 3. 实体类型定义

| type 枚举值 | 中文名 | 说明 | 示例 |
|-------------|--------|------|------|
| `company_name` | 公司名称 | 企业/组织全称 | 深圳市星幻科技有限公司 |
| `person_name` | 人名 | 法定代表人、联系人等 | 张三 |
| `credit_code` | 统一社会信用代码 | 18位编码 | 91440300MA5EXXXXXX |
| `id_card` | 身份证号 | 18位身份证 | 440305199001011234 |
| `phone` | 电话号码 | 手机号或座机号 | 13800138000 |
| `address` | 地址 | 注册地址、经营地址等 | 深圳市南山区科技路1号 |
| `bank_name` | 开户行 | 银行名称 | 招商银行深圳科技园支行 |
| `bank_account` | 银行账号 | 银行卡号/对公账号 | 6225 8888 1234 5678 |
| `email` | 邮箱 | 电子邮箱 | contact@example.com |
| `account_name` | 户名 | 银行账户户名 | 深圳市星幻科技有限公司 |

---

## 4. 角色标识（role）

| role 枚举值 | 说明 | 判定依据 |
|-------------|------|----------|
| `party_a` | 甲方 | 上下文含"甲方/授权方/委托方/出让方/供方/卖方" |
| `party_b` | 乙方 | 上下文含"乙方/获权方/受托方/受让方/需方/买方" |
| `party_c` | 丙方 | 上下文含"丙方/第三方/担保方" |
| `unknown` | 未知方 | 无法从上下文判定 |

**判定逻辑：** 向前搜索 `context_window` 个字符，匹配最近的角色关键词。

---

## 5. 上下文字段（context_field）

标识实体在合同中的语义角色，用于生成更有意义的占位符：

| context_field | 说明 |
|---------------|------|
| `签约主体` | 合同开头的甲乙方名称 |
| `法定代表人` | "法定代表人"后的人名 |
| `联系人` | "联系人"后的人名 |
| `注册地址` | "注册地址/住所"后的地址 |
| `经营地址` | "经营地址/经营场所"后的地址 |
| `联系电话` | "电话/手机"后的号码 |
| `开户行` | "开户行/开户银行"后的银行名 |
| `银行账号` | "账号/帐号"后的数字 |
| `统一社会信用代码` | 对应字段后的编码 |
| `其他` | 无法归类的 |

---

## 6. 错误响应

```json
{
  "success": false,
  "error": {
    "code": "TEXT_TOO_LONG",
    "message": "输入文本超过 100KB 限制"
  }
}
```

| 错误码 | HTTP状态码 | 说明 |
|--------|-----------|------|
| `TEXT_TOO_LONG` | 400 | 文本超过长度限制 |
| `EMPTY_TEXT` | 400 | 文本为空 |
| `INVALID_OPTIONS` | 400 | 参数格式错误 |
| `AUTH_FAILED` | 401 | 认证失败 |
| `RATE_LIMITED` | 429 | 请求频率超限 |
| `INTERNAL_ERROR` | 500 | 服务内部错误 |

---

## 7. 调用方集成示例

API 开发完成后，在合同审核流程中的调用方式：

```python
import requests

# 合同文本（在沙箱 run_python 中读取）
text = "\n".join(p.text for p in doc.paragraphs)

# 调用 NER API
resp = requests.post(
    "https://your-api-host/api/v1/contract/ner",
    headers={"Authorization": "Bearer YOUR_API_KEY"},
    json={"text": text, "options": {"min_confidence": 0.8}}
)

result = resp.json()
entities = result["data"]["entities"]

# 构建脱敏映射
for e in entities:
    placeholder = f"[{e['role']}_{e['context_field']}]"
    # 执行替换...
```

---

## 8. 非功能性需求

| 项目 | 要求 |
|------|------|
| 响应时间 | ≤ 3 秒（10KB 文本） |
| 文本上限 | 100KB / 单次请求 |
| 并发 | 支持至少 10 QPS |
| 认证 | Bearer Token（API Key） |
| 传输安全 | HTTPS（TLS 1.2+） |
| 数据留存 | **不持久化**请求中的合同文本 |
| 字符编码 | UTF-8 |

---

## 9. 推荐技术方案

| 层级 | 推荐 |
|------|------|
| NER 引擎 | spaCy `zh_core_web_trf` / Hugging Face `bert-base-chinese-ner` / 百度 LAC |
| 正则补充 | 统一社会信用代码、身份证、手机号等强格式实体 |
| 角色判定 | 基于上下文窗口的关键词匹配（见第4节） |
| 服务框架 | FastAPI / Flask |
| 部署 | Docker 容器 |

---

## 10. 开发优先级

1. **P0 — 必须**：公司名称、人名、地址、电话、银行账号识别
2. **P1 — 重要**：角色判定（甲方/乙方）、context_field 语义归类
3. **P2 — 增强**：置信度打分、模型版本管理、批量接口
````
