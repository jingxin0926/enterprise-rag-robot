# Enterprise RAG Robot · 企业级智能问答机器人

> 基于 DeepSeek + LangChain + LangGraph 的企业内部知识助手

## 🎯 项目简介

从零构建的**企业级** RAG（Retrieval-Augmented Generation）智能问答系统，具备完整的生产级能力。

## 🧱 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.12 |
| Web 框架 | FastAPI |
| LLM | DeepSeek-V3（兼容 OpenAI 协议） |
| Embedding | BAAI/bge-small-zh-v1.5（ONNX CPU 推理） |
| 向量库 | Qdrant |
| 全文检索 | BM25（jieba 分词） |
| 缓存 | Redis |
| 鉴权 | JWT + bcrypt |
| 部署 | Docker + K8s |

## ✨ 核心能力

- **完整 RAG 链路**：文档解析 → 切片 → Embedding → 向量存储 → 混合检索 → 生成
- **混合检索**：BM25 + Vector + RRF 融合 + Query 改写
- **Agent 编排**：Function Calling 自主决策（知识检索/时间/计算器）
- **流式输出**：SSE 实时打字效果
- **企业工程化**：JWT 鉴权 / 多租户 / 接口限流 / Token 计费 / 链路追踪

## 🚀 快速开始

```bash
# 安装依赖
uv sync

# 配置环境变量
copy .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY

# 启动服务
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000/docs 查看 API 文档

## 📜 License

MIT
