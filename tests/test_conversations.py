"""
Tests for ConversationManager in agy-fleet.
Verifies single ongoing conversation per device, persistence, and auto-rotation when heavy.
"""
import pytest
from orchestrator.conversations import ConversationManager


def test_conversation_persistence(tmp_path):
    storage = str(tmp_path / "convs.json")
    mgr = ConversationManager(storage_path=storage, max_turns=5, max_tokens=1000)

    # Initially empty
    assert mgr.get_conversation_id("pc-naufal") is None

    # Record first turn
    rotated = mgr.record_turn("pc-naufal", "conv-uuid-1", turns=1, total_tokens=100)
    assert rotated is False
    assert mgr.get_conversation_id("pc-naufal") == "conv-uuid-1"

    # Reload in a new manager instance to verify disk persistence
    mgr2 = ConversationManager(storage_path=storage, max_turns=5, max_tokens=1000)
    assert mgr2.get_conversation_id("pc-naufal") == "conv-uuid-1"


def test_auto_rotation_on_turns_limit(tmp_path):
    storage = str(tmp_path / "convs.json")
    mgr = ConversationManager(storage_path=storage, max_turns=3, max_tokens=100000)

    mgr.record_turn("pc-diba", "conv-diba-1", turns=1, total_tokens=500)
    assert mgr.get_conversation_id("pc-diba") == "conv-diba-1"

    mgr.record_turn("pc-diba", "conv-diba-1", turns=2, total_tokens=1000)
    assert mgr.get_conversation_id("pc-diba") == "conv-diba-1"

    # 3rd turn hits max_turns limit -> triggers rotation
    rotated = mgr.record_turn("pc-diba", "conv-diba-1", turns=3, total_tokens=1500)
    assert rotated is True
    # Active conversation cleared so next query starts fresh
    assert mgr.get_conversation_id("pc-diba") is None


def test_auto_rotation_on_tokens_limit(tmp_path):
    storage = str(tmp_path / "convs.json")
    mgr = ConversationManager(storage_path=storage, max_turns=10, max_tokens=2000)

    mgr.record_turn("lap-asus", "conv-asus-1", turns=1, total_tokens=1000)
    assert mgr.get_conversation_id("lap-asus") == "conv-asus-1"

    # Exceeds 2000 tokens limit -> triggers rotation
    rotated = mgr.record_turn("lap-asus", "conv-asus-1", turns=2, total_tokens=2500)
    assert rotated is True
    assert mgr.get_conversation_id("lap-asus") is None


def test_manual_reset(tmp_path):
    storage = str(tmp_path / "convs.json")
    mgr = ConversationManager(storage_path=storage)

    mgr.record_turn("pc-naufal", "conv-1", turns=1, total_tokens=100)
    mgr.record_turn("pc-diba", "conv-2", turns=1, total_tokens=200)

    mgr.reset("pc-naufal")
    assert mgr.get_conversation_id("pc-naufal") is None
    assert mgr.get_conversation_id("pc-diba") == "conv-2"

    mgr.reset_all()
    assert mgr.get_conversation_id("pc-diba") is None
