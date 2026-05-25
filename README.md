# Smart QA System · 智能问答系统

> 基于 DeepSeek 的 RAG 知识问答系统，支持文档检索、混合搜索、Agent 编排。

## 🎯 项目简介

一套面向企业内部场景的 RAG（Retrieval-Augmented Generation）智能问答系统，覆盖从文档接入、向量检索到 Agent 编排的完整链路。

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
| 部署 | Docker + Docker Compose |

## ✨ 核心能力

- **完整 RAG 链路**：文档解析 → 切片 → Embedding → 向量存储 → 混合检索 → 生成
- **混合检索**：BM25 + Vector + RRF 融合 + Query 改写
- **Agent 编排**：Function Calling 自主决策（知识检索 / 时间 / 计算器）
- **流式输出**：SSE 实时打字效果
- **工程化能力**：JWT 鉴权 / 多租户 / 接口限流 / Token 计费 / 链路追踪

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
