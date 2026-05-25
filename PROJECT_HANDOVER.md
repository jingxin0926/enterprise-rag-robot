# 📋 项目交接文档（给新对话/新协作者）

> 本文档让新会话的 AI 助手能快速理解项目背景、当前进度、以及后续工作方向。
> 如果你是新接入这个项目的助手，请先完整阅读本文档。

---

## 一、项目背景

### 我是谁
- 8 年 Java 后端开发经验
- 正在从 Java 后端转型 AI 应用开发工程师
- 目标岗位：AI 应用开发工程师（资深/专家级）
- 目标 JD 要求：RAG 完整链路 / Agent / Function Calling / Hybrid Search / Query Rewriting / 向量库实战

### 这个项目是什么
**企业级 RAG 智能问答机器人** —— 从零搭建的生产级 AI 应用，作为简历项目和面试演示。

**不是玩具项目**，所有设计都按企业级标准来：
- 分层架构（Controller / Service / Infra / Core）
- 完整的异常体系 + 链路追踪 + 日志规范
- 多租户隔离 + JWT 鉴权 + 限流 + Token 计费
- 可一键 Docker 部署

---

## 二、技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.12 |
| 包管理 | uv（不用 pip） |
| Web 框架 | FastAPI |
| LLM | DeepSeek-V3（兼容 OpenAI 协议） |
| Embedding | BAAI/bge-small-zh-v1.5（fastembed + ONNX，CPU 推理） |
| 向量库 | Qdrant（本地持久化模式） |
| 全文检索 | rank-bm25 + jieba 分词（内存版，生产可换 ES） |
| 缓存 | Redis 7（开发阶段降级到内存） |
| 鉴权 | PyJWT + bcrypt |
| 限流 | slowapi |
| Agent 编排 | DeepSeek 原生 Function Calling（不用 LangGraph） |
| 部署 | Docker + Docker Compose |

---

## 三、项目结构

```
enterprise-rag-robot/
├── app/
│   ├── main.py                          # FastAPI 入口
│   ├── api/
│   │   ├── deps.py                      # 依赖注入（鉴权）
│   │   └── v1/
│   │       ├── health.py                # 健康检查
│   │       ├── auth.py                  # 登录/获取用户
│   │       ├── chat.py                  # 普通对话
│   │       ├── knowledge.py             # 知识库 CRUD + RAG 问答
│   │       └── agent.py                 # Agent 智能对话
│   ├── core/
│   │   ├── config.py                    # 配置（pydantic-settings）
│   │   ├── logger.py                    # 日志（loguru）
│   │   ├── exceptions.py                # 异常类 + 错误码
│   │   ├── exception_handler.py         # 全局异常处理
│   │   ├── response.py                  # 统一响应 R<T>
│   │   └── security.py                  # JWT + 密码加密
│   ├── middleware/
│   │   ├── trace.py                     # 链路追踪（trace_id）
│   │   └── rate_limit.py                # 限流
│   ├── domain/
│   │   └── chat_schema.py               # DTO
│   ├── infra/
│   │   ├── llm/deepseek_client.py       # DeepSeek 客户端
│   │   ├── cache/redis_client.py        # Redis 客户端
│   │   └── vector/qdrant_store.py       # Qdrant 向量库
│   └── service/
│       ├── chat_service.py              # 聊天编排
│       ├── document_service.py          # 文档解析+切片
│       ├── rag_service.py               # RAG 编排
│       ├── token_tracker.py             # Token 计费
│       ├── retrieval/
│       │   ├── bm25_retriever.py        # BM25 检索
│       │   ├── hybrid_retriever.py      # 混合检索 + RRF
│       │   ├── reranker.py              # 重排（框架就绪）
│       │   └── query_rewriter.py        # Query 改写
│       └── agent/
│           ├── tools.py                 # Agent 工具定义
│           └── graph.py                 # Agent 编排循环
├── deploy/
│   ├── Dockerfile                       # 镜像构建
│   ├── docker-compose.yml               # 一键部署（app + Redis）
│   ├── deploy.sh                        # 服务器部署脚本
│   ├── server-setup.sh                  # 新服务器初始化
│   └── nginx.conf                       # Nginx 反代配置
├── docs/
│   ├── interview_prep.md                # 面试题库（重要！）
│   └── PROJECT_HANDOVER.md              # 本文档
├── pyproject.toml                       # 依赖
├── .env.example                         # 配置模板
├── README.md                            # 项目说明
└── Dockerfile                           # Docker 构建
```

---

## 四、开发阶段进度（全部完成 ✅）

| 阶段 | 内容 | 关键文件 |
|------|------|---------|
| P0 | 项目脚手架 | `core/`, `middleware/trace.py`, `main.py` |
| P1 | 单模型对话 + 流式 + 会话记忆 | `chat_service.py`, `deepseek_client.py`, `redis_client.py` |
| P2 | RAG 基础 | `document_service.py`, `qdrant_store.py`, `rag_service.py` |
| P3 | 高级检索 | `retrieval/` 整个目录 |
| P4 | Agent 编排 | `agent/tools.py`, `agent/graph.py` |
| P5 | 工程化 | `security.py`, `auth.py`, `rate_limit.py`, `token_tracker.py` |
| P6 | 可观测性 | 日志体系 + trace_id（已内置） |
| P7 | 异步链路 | 当前规模用内存降级，生产再上 Kafka |
| P8 | 上云部署 | `deploy/` 整个目录（Docker Compose 方案） |

---

## 五、当前 API 接口清单

| 接口 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/api/v1/health` | GET | ❌ | 健康检查 |
| `/api/v1/auth/login` | POST | ❌ | 登录获取 JWT |
| `/api/v1/auth/me` | GET | ✅ | 当前用户信息 |
| `/api/v1/chat` | POST | ❌ | 普通对话（带历史） |
| `/api/v1/chat/clear` | POST | ❌ | 清空会话 |
| `/api/v1/knowledge/upload` | POST | ❌ | 上传文档 |
| `/api/v1/knowledge/upload_text` | POST | ❌ | 文本入库 |
| `/api/v1/knowledge/query` | POST | ❌ | RAG 问答 |
| `/api/v1/knowledge/info` | GET | ❌ | 知识库信息 |
| `/api/v1/agent/chat` | POST | 可选 | Agent 智能对话（带计费） |

测试账号：`admin / admin123`、`user1 / user123`、`demo / demo123`

---

## 六、本地启动方法

```powershell
# 必要环境变量
$env:Path = "C:\Users\jingxin\.local\bin;$env:Path"
$env:NO_PROXY = "localhost,127.0.0.1"
$env:HF_ENDPOINT = "https://hf-mirror.com"   # 国内镜像，加速 embedding 模型下载

# 启动
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 访问 Swagger
http://localhost:8000/docs
```

注意：首次启动会下载 ~95MB 的 embedding 模型，需要 1-2 分钟。

---

## 七、当前已知坑/约束

1. **本地代理拦截 localhost** → 必须设 `NO_PROXY=localhost,127.0.0.1`
2. **HuggingFace 国内访问慢** → 用 `HF_ENDPOINT=https://hf-mirror.com` 镜像
3. **bcrypt 5.0 与 passlib 不兼容** → pyproject.toml 已锁定 `bcrypt==4.2.1`
4. **fastembed 当前版本无 TextCrossEncoder** → Reranker 框架就绪但默认未启用
5. **`.doc` 旧格式不支持** → 仅支持 `.docx`，已加友好提示
6. **BM25 内存索引重启丢失** → 需要重新上传文档（向量库不丢）

---

## 八、后续要做的事情

### 短期（1-2周）
1. ✅ 推 GitHub（已完成）
2. ⏳ 租云服务器（用户计划下周做）
3. ⏳ 用 `deploy/deploy.sh` 上云部署
4. ⏳ 准备面试（参考 `docs/interview_prep.md`）

### 中期（投简历后）
1. 加 Langfuse 接入（监控所有 LLM 调用）
2. 加自动评测集（准确率/召回率指标）
3. 写一篇技术博客讲架构设计（GitHub README 链接到博客）

### 长期（入职后）
1. 上 Kafka 异步处理大批量文档
2. 接入 K8s 部署
3. 数字人项目（用户后续想做的方向，需要 GPU）

---

## 九、用户当前学习状态

- 跟着把整个项目代码**敲了一遍**（不是复制，是手敲）
- AI 助手**逐文件解释了每个模块的代码**（用 Java 类比方式讲解）
- 用户表示"理解了但没记住细节"——**正常状态**
- 用户已**不打算再敲代码**，未来工作主要靠 AI 辅助编码

### 用户的核心痛点
- 没有 AI 应用开发的实际项目经验
- 不知道面试官会问什么深度的问题
- 担心入职后无法独立工作

### 用户的优势
- 8 年 Java 工程化经验（其他候选人通常缺这个）
- 能理解架构设计和 tradeoff
- 完整跟敲了项目，看代码能看懂

---

## 十、个人偏好/规则（来自用户的全局配置）

- **语言**：全程中文，必要英文术语保留并配中文解释
- **代码注释**：示例代码必须完整注释，遵循 JavaDoc 风格
- **平台**：默认 Windows 环境，路径用 `\`
- **风格**：结论先行、简洁直达，必要时用 emoji 增加亲和力
- **技术对比**：用户熟悉 Java/Spring Boot/MySQL/Nacos/Kafka/Redis，讲解时**优先用 Java 类比**
- **流程图**：优先用 Mermaid 格式
- **首条回复首行**：仅在新对话首条回复输出"我是你的AI助手，懂你的规矩，让我带你一起飞🚀"

---

## 十一、新会话开场白模板

如果用户在新会话里说"继续我们的项目"或类似的话，AI 助手应该：

1. 输出首行问候（按全局规则）
2. 简要确认：已阅读 `PROJECT_HANDOVER.md`，了解到这是已完成的企业级 RAG 项目
3. 问用户：今天想做什么？给三个常见方向供选择：
   - 上云部署（按 `deploy/deploy.sh` 流程）
   - 面试准备（按 `docs/interview_prep.md` 模拟问答）
   - 加新功能（语义缓存/Langfuse 监控/微调向量模型等）

---

## 十二、补充说明

- **GitHub 仓库**：https://github.com/jingxin0926/enterprise-rag-robot
- **DeepSeek API Key 已配置**（在 `.env` 文件，gitignore 中）
- **已花费**：约 ¥1（DeepSeek API），阶段性成果验证完整
- **备份项目**：`d:\work\python\robot`（最初的演示项目，不再维护）
- **当前项目**：`d:\work\python\enterprise-rag-robot`（手敲版，后续开发用这个）

---

## 📌 给新会话 AI 助手的最后提醒

1. 不要把这个项目当成 Demo——要按"生产级系统"的标准对待
2. 用户是 Java 老兵，**不要解释基础概念**（比如什么是泛型、什么是 IoC）
3. 用户喜欢**对照 Java 讲解**，比如 "这相当于 Spring 的 @Service"
4. 不要长篇大论，**结论先行**，需要细节用户会追问
5. 部署阶段如果用户没经验，**给具体命令**，不要只说"配置 xxx"
