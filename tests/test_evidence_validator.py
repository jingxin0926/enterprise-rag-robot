"""证据判定器 JSON 解析测试。"""

from app.service.retrieval.evidence_validator import EvidenceValidator


def test_evidence_validator_parses_structured_verdict() -> None:
    """判定器应读取模型返回的充分性结论。"""
    verdict = EvidenceValidator.parse_response('{"sufficient": true, "reason": "接口信息明确"}')

    assert verdict.sufficient is True
    assert verdict.reason == "接口信息明确"


def test_evidence_validator_fails_closed_on_invalid_json() -> None:
    """模型格式异常时必须拒答，避免异常放行。"""
    verdict = EvidenceValidator.parse_response("not-json")

    assert verdict.sufficient is False
