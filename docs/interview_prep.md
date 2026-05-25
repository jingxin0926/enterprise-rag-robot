# AI 应用开发工程师面试准备

## 一、高频面试题（按 JD 逐条对标）

### 🔥 RAG 链路（必问，占 40%）

**Q1: 描述一下你做的 RAG 系统的完整链路？**

> 标准答案框架：
> 文档上传 → 解析（PDF/Word/TXT）→ 切片（RecursiveCharacterTextSplitter，500字+100重叠）
> → Embedding（bge-small-zh，ONNX CPU 推理）→ 存入向量库（Qdrant）
> → 用户提问 → Query 改写（DeepSeek 优化查询）→ 混合检索（BM25 + Vector）
> → RRF 融合排序 → 组装 Prompt（system + context + question）→ LLM 生成回答

**Q2: 为什么要做文档切片？chunk_size 怎么选？overlap 为什么要有？**

> - 切片原因：LLM 上下文窗口有限 + 检索粒度越小越精确
> - chunk_size 500：经验值，太小丢失语义上下文，太大噪音多
> - overlap 100：防止句子被切断，保证语义连续性
> - 实际调优方法：准备评测集，用不同参数跑，看召回率和准确率

**Q3: 纯向量检索有什么问题？你怎么解决的？**

> 问题：
> - 专有名词不敏感（"ISO9001" 向量找不到）
> - 数字精确匹配差
> - 短查询语义模糊
>
> 解决方案：混合检索
> - BM25 补充关键词精确匹配
> - RRF 融合两路结果
> - Query 改写提升查询质量

**Q4: RRF 融合算法怎么工作的？为什么不用简单加权？**

> RRF 公式：score = Σ weight/(k+rank+1)
> - 只看排名不看原始分数（BM25 和向量分数量纲不同，不可直接相加）
> - k=60 是经验常数，防止第 1 名分数过大
> - 同一篇文档被两路都召回 → 分数叠加 → 排名靠前

**Q5: Embedding 模型怎么选的？为什么不用 OpenAI 的？**

> - 选了 bge-small-zh-v1.5：中文优化、512 维、90MB 体积
> - 不用 OpenAI：1）网络延迟 2）成本高 3）数据安全（企业内部文档不想送出国）
> - 用 fastembed（ONNX Runtime）本地 CPU 推理，无需 GPU


### 🔥 Agent / Function Calling（必问，占 25%）

**Q6: 你的 Agent 是怎么实现的？为什么没用 LangChain 的 AgentExecutor？**

> - 基于 DeepSeek 原生 Function Calling 协议实现
> - 没用 LangChain AgentExecutor 的原因：
>   1）对 DeepSeek 兼容性更好（原生 OpenAI 协议）
>   2）更轻量，没有额外抽象层
>   3）调试更方便，能看到每一步的消息流
> - 核心是 Agent 循环：LLM 判断 → 调工具 → 反馈结果 → 再判断 → 最终回答

**Q7: Agent 会不会出现死循环？怎么防止？**

> - 设置 MAX_TOOL_ROUNDS = 3，超过强制结束
> - 最后一轮不提供 tools 参数，强制 LLM 直接回答
> - 记录每轮 token 消耗，超限熔断

**Q8: 怎么让 Agent 正确选择工具？**

> - 关键在 docstring：每个工具函数的文档字符串就是"说明书"
> - system prompt 里明确规则："公司制度问题必须先检索"
> - 工具描述要精准，不能模糊


### 🔥 工程化（必问，占 20%）

**Q9: 多租户隔离怎么做的？**

> - JWT Token 中携带 tenant_id
> - 每次请求从 token 解出 tenant_id
> - 后续数据库查询/向量库检索都带 tenant_id 过滤
> - 不同租户数据物理/逻辑隔离

**Q10: 接口限流怎么做的？为什么 Agent 接口限制更严格？**

> - 用 slowapi（基于 limits 库），按 IP 限流
> - 默认 60 次/分钟，Agent 接口 20 次/分钟
> - Agent 更严格因为：每次调用消耗 LLM token（成本高），且可能多轮调用

**Q11: Token 计费怎么做的？**

> - 每次 LLM 调用后记录 prompt_tokens + completion_tokens
> - 按 tenant_id / user_id 汇总
> - 估算费用：输入 ¥0.5/百万token，输出 ¥2/百万token
> - 后续可对接账单系统，做配额管理

**Q12: 流式输出怎么实现的？**

> - SSE（Server-Sent Events）协议
> - FastAPI 的 EventSourceResponse
> - LLM SDK 返回 stream=True，逐 chunk yield
> - 前端用 EventSource 或 fetch + ReadableStream 消费


### 🔥 架构设计（中高频，占 15%）

**Q13: 项目分层架构是怎样的？为什么这样分？**

> - api/ → Controller 层（路由 + 参数校验）
> - service/ → 业务编排层（核心逻辑）
> - infra/ → 基础设施层（LLM客户端、向量库、缓存）
> - core/ → 通用基础（配置、日志、异常、安全）
> - 原因：关注点分离，换向量库只改 infra 层，业务不动

**Q14: 如果要支持多模型切换（DeepSeek/OpenAI/Claude），你会怎么设计？**

> - 抽象 LLMClient 接口（协议类/ABC）
> - 每个模型一个实现类
> - 通过配置/路由策略选择具体实现
> - 支持 Fallback：主模型超时自动切备用模型

**Q15: 如果知识库有 100 万文档，你的方案还能用吗？要改什么？**

> - BM25：从内存版切换为 Elasticsearch
> - 向量库：Qdrant 本地模式 → Qdrant Server 集群（或 Milvus）
> - Embedding：批量异步处理（Kafka + Worker）
> - 索引分片 + 租户级别独立 collection


---

## 二、你该怎么准备（行动计划）

### 第一优先级：能说清楚项目（1-2天）

1. **画架构图**：在纸上画出完整链路，能白板讲 5 分钟
2. **记住关键数字**：chunk_size=500, overlap=100, bge-512维, RRF k=60
3. **准备 3 个故事**：
   - 一个"技术选型"故事（为什么选 Qdrant 不选 Milvus）
   - 一个"踩坑"故事（向量检索对专有名词不敏感 → 加 BM25）
   - 一个"优化"故事（纯向量检索 → 混合检索，效果提升）

### 第二优先级：能跑 Demo 演示（已完成）

- 面试时打开 Postman/Swagger 现场演示
- 上传文档 → 提问 → 看到带引用的回答
- 展示 Agent 自动选择工具

### 第三优先级：补充理论知识（3-5天）

| 主题 | 看什么 | 时长 |
|------|--------|------|
| RAG 理论 | B站搜"RAG 原理"前 3 个视频 | 2小时 |
| Embedding 原理 | 了解 Bi-Encoder vs Cross-Encoder | 1小时 |
| LangChain 核心概念 | 官方文档 Concepts 章节 | 2小时 |
| Prompt Engineering | DeepSeek 官方 Cookbook | 1小时 |
| 向量数据库对比 | Qdrant vs Milvus vs Pinecone 对比文章 | 1小时 |

### 第四优先级：工作中靠 AI 辅助编码

> 实话说：这个岗位日常 80% 的代码可以用 Kiro / Cursor / Copilot 辅助生成。
> 你需要的核心能力是：
> 1. **知道要做什么**（架构设计、技术选型）
> 2. **能看懂代码**（review AI 生成的代码，判断对不对）
> 3. **能调试问题**（看日志、看报错、定位问题）
> 4. **能跟面试官聊设计**（为什么这样做、有什么 tradeoff）

你跟着敲了一遍代码 + 我逐行解释了每个模块，2 和 3 已经具备了。
重点补 1 和 4——就是上面的面试题。

---

## 三、面试时的话术模板

### 自我介绍（30秒版）

> "我有 8 年 Java 后端开发经验，最近半年转型 AI 应用开发方向。
> 我从零搭建了一套企业级 RAG 智能问答系统，基于 Python + FastAPI + DeepSeek。
> 核心能力包括：完整的 RAG 链路、混合检索、Agent 自主决策、以及企业级的鉴权限流体系。
> 我的优势是：8 年工程化经验让我更理解什么叫'生产级'——不只是 Demo 能跑，
> 而是有异常处理、链路追踪、多租户隔离这些企业需要的东西。"

### 被问"你之前没有 AI 经验，为什么转型？"

> "AI 应用开发的核心不只是算法——更是工程化交付。
> 8 年 Java 给了我微服务架构、高可用设计、生产级代码质量的积累。
> 这些在 AI 应用领域反而是稀缺能力——很多 AI 背景的同学做出来的系统工程质量不够。
> 我的定位是：把 AI 能力以企业级标准交付出去。"

### 被问不会的问题

> "这个我确实还没深入实践过，但我的理解是 xxx。
> 如果需要落地，我的做法是先看官方文档 + 找一个 benchmark 验证，
> 您这边有推荐的方向吗？"（反问拉近距离）
