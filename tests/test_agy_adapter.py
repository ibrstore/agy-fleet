"""
Unit tests for Antigravity CLI (agy) Adapter.
"""
import pytest
from daemon.agy_adapter import AgyAdapter
from shared.redactor import Redactor


def test_extract_json_response():
    adapter = AgyAdapter()
    sample_stdout = (
        "[agy] Loading workspace...\n"
        "[agy] Initializing conversation...\n"
        '{"conversation_id":"conv-123","status":"SUCCESS","response":"Hello from agy!","error":""}\n'
    )
    result = adapter._extract_json_response(sample_stdout)
    assert result is not None
    assert result["status"] == "SUCCESS"
    assert result["conversation_id"] == "conv-123"
    assert result["response"] == "Hello from agy!"


def test_extract_json_response_with_trailing_lines():
    adapter = AgyAdapter()
    sample_stdout = (
        '{"conversation_id":"conv-456","status":"ERROR","response":"","error":"Timeout"}\n'
        "[agy] process finished with code 1\n"
    )
    result = adapter._extract_json_response(sample_stdout)
    assert result is not None
    assert result["status"] == "ERROR"
    assert result["error"] == "Timeout"


def test_extract_json_response_invalid_fallback():
    adapter = AgyAdapter()
    sample_stdout = "Just plain text output without JSON"
    result = adapter._extract_json_response(sample_stdout)
    assert result is None


def test_adapter_redaction_integration(redactor_with_vault: Redactor):
    adapter = AgyAdapter(redactor=redactor_with_vault)
    sample_stdout = (
        '{"conversation_id":"c-1","status":"SUCCESS","response":"Used SuperSecretDBPass123 successfully","error":""}'
    )
    parsed = adapter._extract_json_response(adapter.redactor.redact(sample_stdout))
    assert parsed is not None
    assert "SuperSecretDBPass123" not in parsed["response"]
    assert "[REDACTED]" in parsed["response"]
