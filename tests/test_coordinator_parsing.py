"""
Unit tests for coordinator node parsing, chain detection, and markdown stripping.
"""
import pytest
from unittest.mock import MagicMock
from orchestrator.coordinator import FleetCoordinator


@pytest.fixture
def coordinator():
    # Mock bot, presence, nats, etc.
    coord = object.__new__(FleetCoordinator)
    coord.presence = MagicMock()
    coord.presence.get_online_nodes.return_value = ["vps", "pc-diba", "pc-naufal"]
    return coord


def test_parse_target_nodes_single(coordinator):
    nodes, rest = coordinator._parse_target_nodes("@pc-diba: cek spek")
    assert nodes == ["pc-diba"]
    assert rest == "cek spek"


def test_parse_target_nodes_with_connector_lalu(coordinator):
    text = "@pc-diba lalu @vps tp kan jaringan di ip cctv itu up to 300 mbps"
    nodes, rest = coordinator._parse_target_nodes(text)
    assert nodes == ["pc-diba", "vps"]
    assert rest == "tp kan jaringan di ip cctv itu up to 300 mbps"


def test_parse_target_nodes_with_connector_dan(coordinator):
    text = "@pc-diba dan @vps cek disk"
    nodes, rest = coordinator._parse_target_nodes(text)
    assert nodes == ["pc-diba", "vps"]
    assert rest == "cek disk"


def test_parse_target_nodes_with_arrow_chain(coordinator):
    text = "@pc-diba -> @vps -> @pc-naufal: halo kawan"
    nodes, rest = coordinator._parse_target_nodes(text)
    assert nodes == ["pc-diba", "vps", "pc-naufal"]
    assert rest == "halo kawan"


def test_is_chain_request(coordinator):
    raw_prompt = "@pc-diba lalu @vps tp kan jaringan cctv..."
    cleaned_prompt = "tp kan jaringan cctv..."
    assert coordinator._is_chain_request(cleaned_prompt, ["pc-diba", "vps"], raw_text=raw_prompt) is True

    raw_dan = "@pc-diba dan @vps cek disk"
    cleaned_dan = "cek disk"
    assert coordinator._is_chain_request(cleaned_dan, ["pc-diba", "vps"], raw_text=raw_dan) is False

    raw_arrow = "@pc-diba -> @vps: analisa arsitektur"
    assert coordinator._is_chain_request("analisa arsitektur", ["pc-diba", "vps"], raw_text=raw_arrow) is True


def test_strip_markdown():
    raw = (
        "### Ringkasan Arsitektur\n"
        "**Decoder Protokol**: Aktif di `server.js`\n"
        "*Italic text* and _other italic_\n"
        "```python\nprint('hello')\n```"
    )
    clean = FleetCoordinator._strip_markdown(raw)
    assert "###" not in clean
    assert "**" not in clean
    assert "print('hello')" in clean
    assert "Ringkasan Arsitektur" in clean


def test_build_multi_node_preview(coordinator):
    from orchestrator.dlp_filter import TelegramDLPFilter
    coordinator.dlp = TelegramDLPFilter()
    coordinator.conv_mgr = MagicMock()
    coordinator.conv_mgr.max_turns = 30

    header = "🔗 *HASIL DISKUSI BERANTAI*"
    nodes = ["pc-diba", "vps"]
    results = {
        "pc-diba": {"agy": {"response": "A" * 3000}},
        "vps": {"agy": {"response": "B" * 3000}},
    }

    preview, exceeds, full = coordinator._build_multi_node_preview(header, nodes, results, max_len=3500)
    assert exceeds is True
    assert "Node `pc-diba`" in preview
    assert "Node `vps`" in preview
    assert len(preview) <= 3500
    assert "A" * 3000 in full
    assert "B" * 3000 in full


def test_extract_mode_override():
    # Test --fast / --low
    m, rest = FleetCoordinator._extract_mode_override("cek status disk --fast")
    assert m == "fast"
    assert rest == "cek status disk"

    m, rest = FleetCoordinator._extract_mode_override("--low analisa memori")
    assert m == "fast"
    assert rest == "analisa memori"

    # Test --deep / --high
    m, rest = FleetCoordinator._extract_mode_override("analisa arsitektur cctv --deep")
    assert m == "deep"
    assert rest == "analisa arsitektur cctv"

    m, rest = FleetCoordinator._extract_mode_override("--high rancang database")
    assert m == "deep"
    assert rest == "rancang database"

    # Test --balanced / --medium
    m, rest = FleetCoordinator._extract_mode_override("hitung perkiraan trafik --balanced")
    assert m == "balanced"
    assert rest == "hitung perkiraan trafik"

    # Test without flags
    m, rest = FleetCoordinator._extract_mode_override("halo selamat pagi")
    assert m is None
    assert rest == "halo selamat pagi"


def test_reasoning_mode_persistence(tmp_path, monkeypatch):
    test_settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("orchestrator.coordinator.SETTINGS_DIR", tmp_path)
    monkeypatch.setattr("orchestrator.coordinator.SETTINGS_FILE", test_settings_file)

    # Initially defaults to deep
    assert FleetCoordinator._get_active_reasoning_mode() == "deep"

    # Set to fast
    assert FleetCoordinator._set_reasoning_mode("fast") is True
    assert FleetCoordinator._get_active_reasoning_mode() == "fast"

    # Set to balanced
    assert FleetCoordinator._set_reasoning_mode("balanced") is True
    assert FleetCoordinator._get_active_reasoning_mode() == "balanced"

    # Invalid mode returns False and doesn't overwrite
    assert FleetCoordinator._set_reasoning_mode("invalid_mode") is False
    assert FleetCoordinator._get_active_reasoning_mode() == "balanced"


def test_mode_keyboard_and_text():
    kb = FleetCoordinator._build_mode_keyboard("deep")
    assert len(kb.inline_keyboard) == 2
    # Check deep has checked radio button
    assert "🔘" in kb.inline_keyboard[1][0].text
    assert "fleet_mode:deep" == kb.inline_keyboard[1][0].callback_data

    txt = FleetCoordinator._format_mode_text("deep")
    assert "Deep Reasoning (High)" in txt
    assert "gemini-3.8-flash-high" in txt
