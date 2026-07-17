# Smart QA System

基于 RAG 的智能问答系统，支持多租户知识库管理、Multi-Agent 智能对话、任务规划与自动执行。

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                        客户端                                │
│              Web / 移动端 / API 直调                          │
└─────────────────────┬───────────────────────────────────────┘
                      │ HTTPS
┌─────────────────────▼───────────────────────────────────────┐
│                   API Gateway (FastAPI)                       │
│  鉴权(JWT) | 限流 | 链路追踪(trace_id) | 多租户识别           │
└─────┬──────────┬──────────┬──────────┬──────────────────────┘
      │          │          │          │
┌─────▼────┐ ┌──▼───┐ ┌───▼────┐ ┌───▼──────────┐
│ RAG 服务  │ │Agent │ │ 评测   │ │ Prompt 管理   │
│ 混合检索  │ │Multi │ │LLM-as  │ │ 在线编辑     │
│ 语义缓存  │ │Plan  │ │Judge   │ │ 多租户定制    │
└─────┬────┘ └──┬───┘ └───┬────┘ └──────────────┘
      │         │          │
┌─────▼─────────▼──────────▼──────────────────────┐
│                基础设施层                          │
│  Qdrant(向量库) | Redis(缓存) | DeepSeek(LLM)     │
│  Langfuse(可观测) | BM25(关键词索引)               │
└─────────────────────────────────────────────────┘
```

## 核心能力

| 模块 | 能力 | 说明 |
|------|------|------|
| RAG | 混合检索 | 向量检索(Qdrant) + BM25关键词 + RRF融合 |
| RAG | Query 改写 | LLM 将口语化问题转为检索友好表述 |
| RAG | 语义缓存 | 相似问题命中缓存直接返回，降低 Token 消耗 30-60% |
| RAG | 质量评测 | LLM-as-Judge 三维度自动打分(Faithfulness/Relevancy/Precision) |
| Agent | 统一入口 | 自动判断复杂度，选择最佳处理策略 |
| Agent | Multi-Agent | Supervisor 路由 + 专家Agent(知识/对话/数据) |
| Agent | Task Planning | 复杂任务自动拆解为子任务，按依赖顺序执行 |
| Agent | 记忆系统 | 双层架构：短期原文 + 长期摘要压缩 |
| 工程化 | 多租户隔离 | 数据/Prompt/计费/会话 全维度隔离 |
| 工程化 | Prompt 管理 | 结构化Markdown + 在线编辑API + 缓存热重载 |
| 工程化 | 可观测性 | Langfuse LLM调用追踪 + Token计费统计 |
| 工程化 | 安全 | JWT鉴权 + 接口限流 + Prompt Injection 防御 |

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.12 |
| Web 框架 | FastAPI |
| LLM | DeepSeek (兼容 OpenAI 协议) |
| Embedding | BAAI/bge-small-zh-v1.5 (fastembed + ONNX, CPU推理) |
| 向量库 | Qdrant |
| 全文检索 | rank-bm25 + jieba 分词 |
| 文本切分 | langchain-text-splitters (RecursiveCharacterTextSplitter) |
| 业务元数据 | MySQL 8.0 |
| 缓存 | Redis 7 |
| 鉴权 | PyJWT + bcrypt |
| 限流 | slowapi |
| 可观测性 | Langfuse |
| 部署 | Docker + Docker Compose |

## 项目结构

```
app/
├── api/v1/              # 接口层（RESTful API）
│   ├── agent.py         # 智能对话（统一入口 + Multi-Agent + Planning）
│   ├── auth.py          # 认证（登录/用户信息）
│   ├── chat.py          # 普通对话
│   ├── eval.py          # RAG 质量评测
│   ├── knowledge.py     # 知识库管理（上传/查询/信息）
│   └── prompt_manage.py # Prompt 在线管理
├── core/                # 核心基础设施
│   ├── config.py        # 配置加载（pydantic-settings）
│   ├── security.py      # JWT + 密码加密
│   ├── tenant.py        # 多租户上下文管理
│   ├── exceptions.py    # 异常体系 + 错误码
│   └── response.py      # 统一响应 R<T>
├── infra/               # 基础设施对接
│   ├── llm/             # DeepSeek 客户端
│   ├── cache/           # Redis 客户端
│   ├── vector/          # Qdrant 向量库
│   └── observability/   # Langfuse 可观测性
├── middleware/          # 中间件
│   ├── trace.py         # 链路追踪
│   ├── tenant.py        # 多租户识别
│   └── rate_limit.py    # 限流
├── prompts/             # Prompt 模板（结构化 Markdown）
│   ├── rag_system.md
│   ├── agent_system.md
│   ├── supervisor.md
│   ├── task_planner.md
│   └── tenants/         # 租户定制 Prompt
├── service/             # 业务逻辑层
│   ├── rag_service.py   # RAG 编排
│   ├── chat_service.py  # 对话编排
│   ├── eval_service.py  # 评测服务
│   ├── memory.py        # 对话记忆管理
│   ├── semantic_cache.py # 语义缓存
│   ├── agent/           # Agent 模块
│   │   ├── graph.py     # 单Agent循环
│   │   ├── multi_agent.py # Multi-Agent协作
│   │   ├── task_planner.py # 任务拆解规划
│   │   └── tools.py     # 工具定义
│   └── retrieval/       # 检索模块
│       ├── hybrid_retriever.py # 混合检索 + RRF
│       ├── bm25_retriever.py   # BM25 关键词检索
│       ├── query_rewriter.py   # Query 改写
│       └── reranker.py         # 重排序
└── main.py              # 应用入口
```

## 快速开始

### 环境要求

- Python 3.12+
- uv (包管理器)
- Redis 7 (可选，不启动时自动降级到内存)
- Qdrant Server（生产推荐；本地开发不配置时使用文件模式）

### 本地启动

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY 和 JWT_SECRET_KEY

# 3. 启动服务
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. 访问 API 文档
open http://localhost:8000/docs
```

### Docker 部署

```bash
# 一键部署（含 Nginx + FastAPI + Redis + Qdrant）
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build

# 健康检查
curl http://localhost:8000/api/v1/health
```

生产部署还需在 `.env` 中配置 `MYSQL_PASSWORD` 和 `MYSQL_ROOT_PASSWORD`。MySQL 仅在 Docker 内网开放，保存知识库、文档、入库任务、切片元数据与操作审计；Qdrant 仅保存向量和检索 payload。

部署完成后：

- 前端页面：`http://服务器公网IP/`
- 后端健康检查：`http://服务器公网IP/api/v1/health`
- FastAPI 容器端口仅绑定本机 `127.0.0.1:8000`，公网统一走 Nginx。

### Qdrant 部署模式

本地开发默认使用 `data/qdrant_storage` 文件模式，便于零成本启动。
云上部署推荐使用独立 Qdrant Server，应用通过环境变量连接：

```bash
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=your-qdrant-api-key
```

`deploy/docker-compose.yml` 已内置 Qdrant 服务，向量数据持久化到 `qdrant-data` 卷，并且不对公网暴露 `6333` 端口。

## API 接口

| 接口 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/api/v1/health` | GET | - | 健康检查 |
| `/api/v1/auth/login` | POST | - | 登录获取 JWT |
| `/api/v1/auth/me` | GET | ✅ | 当前用户信息 |
| `/api/v1/agent/chat` | POST | ✅ | 智能对话（统一入口） |
| `/api/v1/knowledge/upload` | POST | ✅ | 上传文档到知识库 |
| `/api/v1/knowledge/documents` | GET | ✅ | 分页查询文档处理状态 |
| `/api/v1/knowledge/documents/{document_id}` | DELETE | ✅ | 删除文档及其向量切片 |
| `/api/v1/knowledge/backfill-legacy` | POST | 管理员 | 回填历史 Qdrant 向量元数据 |
| `/api/v1/knowledge/query` | POST | ✅ | 知识库 RAG 问答 |
| `/api/v1/knowledge/info` | GET | ✅ | 知识库信息 |
| `/api/v1/eval/single` | POST | - | 单条 RAG 质量评测 |
| `/api/v1/eval/rag` | POST | - | 端到端 RAG 评测 |
| `/api/v1/prompts` | GET | ✅ | 查看所有 Prompt |
| `/api/v1/prompts/{name}` | PUT | ✅ | 编辑 Prompt |

## License

MIT
