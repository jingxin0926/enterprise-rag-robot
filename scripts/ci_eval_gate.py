"""
CI/CD 质量门禁脚本

用途：
    接入 CI/CD 流水线（GitHub Actions / Jenkins），每次代码提交或 Prompt 修改后
    自动跑 RAG 评测，分数低于阈值则构建失败，阻止上线。

工作流程：
    1. 启动服务（或连接已有的测试环境）
    2. 灌入标准测试集
    3. 对每个问题跑完整 RAG 流程
    4. LLM-as-Judge 打分
    5. 与阈值比较，决定 exit code

使用方式：
    uv run python scripts/ci_eval_gate.py

    退出码：
    - 0: 评测通过（分数达标）
    - 1: 评测未通过（分数低于阈值）

环境变量：
    EVAL_BASE_URL: 服务地址（默认 http://localhost:8000）
    EVAL_THRESHOLD: 最低通过分数（默认 3.5，满分5）
"""

import os
import sys
import json

import httpx

# 配置
BASE_URL = os.getenv("EVAL_BASE_URL", "http://localhost:8000")
THRESHOLD = float(os.getenv("EVAL_THRESHOLD", "3.5"))  # 最低通过分数（1-5）

# 标准评测集（覆盖不同类型的问题）
TEST_QUESTIONS = [
    "公司年假怎么申请？",
    "费用报销流程是什么？报销标准是多少？",
    "代码审查有哪些规范要求？",
    "新员工入职需要准备什么材料？",
    "公司的考勤打卡规则是怎样的？",
    "数据库设计有哪些命名规范？",
    "线上故障怎么分级？响应时间要求是什么？",
    "Git分支命名规范是什么？",
    "部署发布的流程是怎样的？",
    "接口限流策略是什么？每分钟限多少次？",
]


def run_eval() -> dict:
    """调用评测接口，返回评测结果"""
    url = f"{BASE_URL}/api/v1/eval/rag"
    print(f"📡 调用评测接口: {url}")
    print(f"📋 评测问题数: {len(TEST_QUESTIONS)}")
    print("-" * 60)

    resp = httpx.post(
        url,
        json={"questions": TEST_QUESTIONS},
        timeout=120.0,  # 评测涉及多次LLM调用，给足时间
    )

    if resp.status_code != 200:
        print(f"❌ 评测接口返回异常: HTTP {resp.status_code}")
        print(resp.text)
        sys.exit(1)

    return resp.json()


def print_report(data: dict) -> None:
    """打印评测报告"""
    eval_data = data.get("data", {})

    print("\n" + "=" * 60)
    print("📊 RAG 质量评测报告")
    print("=" * 60)
    print(f"  评测总数:       {eval_data.get('total', 0)}")
    print(f"  忠实度均分:     {eval_data.get('avg_faithfulness', 0):.2f} / 5.0")
    print(f"  相关性均分:     {eval_data.get('avg_relevancy', 0):.2f} / 5.0")
    print(f"  检索精确度均分: {eval_data.get('avg_context_precision', 0):.2f} / 5.0")
    print(f"  总体均分:       {eval_data.get('overall_avg', 0):.2f} / 5.0")
    print(f"  通过阈值:       {THRESHOLD:.2f}")
    print("=" * 60)

    # 逐条结果
    results = eval_data.get("results", [])
    print("\n📝 逐条评测明细:")
    print("-" * 60)
    for i, r in enumerate(results, 1):
        q = r.get("question", "")[:40]
        avg = r.get("avg_score", 0)
        status = "✅" if avg >= THRESHOLD else "❌"
        print(f"  {status} [{i:2d}] {q:<40s} → {avg:.1f}/5")

        # 打印各维度细节
        for s in r.get("scores", []):
            print(f"       {s['metric']}: {s['score']}/5 | {s.get('reason', '')[:50]}")
        print()


def main():
    """主入口"""
    print("🚀 CI/CD 质量门禁 - RAG 评测")
    print(f"   服务地址: {BASE_URL}")
    print(f"   通过阈值: {THRESHOLD}/5.0")
    print()

    # 执行评测
    result = run_eval()
    print_report(result)

    # 判定结果
    eval_data = result.get("data", {})
    overall_avg = eval_data.get("overall_avg", 0)

    if overall_avg >= THRESHOLD:
        print(f"\n🎉 评测通过！总分 {overall_avg:.2f} ≥ 阈值 {THRESHOLD}")
        print("   ✅ 允许上线")
        sys.exit(0)
    else:
        print(f"\n💥 评测未通过！总分 {overall_avg:.2f} < 阈值 {THRESHOLD}")
        print("   ❌ 阻止上线，请检查 Prompt 或检索质量")
        sys.exit(1)


if __name__ == "__main__":
    main()
