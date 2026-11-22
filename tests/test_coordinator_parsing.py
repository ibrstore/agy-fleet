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

    # Case 1: Exceeds 3000 chars -> Clean redirect notice, no messy text in chat, full in md
    results = {
        "pc-diba": {"agy": {"response": "A" * 2000}},
        "vps": {"agy": {"response": "B" * 2000}},
    }
    preview, exceeds, full = coordinator._build_multi_node_preview(header, nodes, results, max_len=3000)
    assert exceeds is True
    assert "dokumen `.md` terlampir" in preview
    assert "Analisis Selesai" in preview
    # Full response text must NOT be dumped into preview
    assert "A" * 2000 not in preview
    # But must exist in full document
    assert "A" * 2000 in full
    assert "B" * 2000 in full

    # Case 2: Under 3000 chars -> Direct text sent, NO file attachment
    short_results = {
        "pc-diba": {"agy": {"response": "Hasil analisis singkat PC Diba."}},
        "vps": {"agy": {"response": "Hasil analisis singkat VPS."}},
    }
    preview_short, exceeds_short, full_short = coordinator._build_multi_node_preview(header, nodes, short_results, max_len=3000)
    assert exceeds_short is False
    assert preview_short == full_short
    assert "Hasil analisis singkat PC Diba." in preview_short
    assert "dokumen `.md` terlampir" not in preview_short


def test_extract_mode_override():
    # Test --auto / --smart
    m, rest = FleetCoordinator._extract_mode_override("cek status disk --auto")
    assert m == "auto"
    assert rest == "cek status disk"

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


def test_classify_prompt_complexity():
    # Light / Summary / Status -> fast (low effort, snappy responses)
    assert FleetCoordinator._classify_prompt_complexity("cek ram") == "fast"
    assert FleetCoordinator._classify_prompt_complexity("status disk") == "fast"
    assert FleetCoordinator._classify_prompt_complexity("halo selamat pagi") == "fast"
    assert FleetCoordinator._classify_prompt_complexity("port berapa yg kebuka") == "fast"
    assert FleetCoordinator._classify_prompt_complexity("rangkum arsitektur cctv kemarin") == "fast"
    assert FleetCoordinator._classify_prompt_complexity("ringkas hasil diskusi") == "fast"

    # Discussion / Comparative analysis / Chain -> balanced (medium effort)
    assert FleetCoordinator._classify_prompt_complexity("analisa arsitektur cctv shinobi") == "balanced"
    assert FleetCoordinator._classify_prompt_complexity("bagaimana cara rancang cluster docker tailscale") == "balanced"
    assert FleetCoordinator._classify_prompt_complexity("troubleshoot error webrtc streaming") == "balanced"
    assert FleetCoordinator._classify_prompt_complexity("diskusikan perbandingan port", is_chain=True) == "balanced"

    # Pengerjaan / Eksekusi / Setup / Sistem -> deep (high effort, 600s)
    assert FleetCoordinator._classify_prompt_complexity("eksekusi setup cctv shinobi") == "deep"
    assert FleetCoordinator._classify_prompt_complexity("pasang docker compose dan jalankan container") == "deep"
    assert FleetCoordinator._classify_prompt_complexity("kerjakan migrasi database dan tulis skrip") == "deep"
    assert FleetCoordinator._classify_prompt_complexity("/deploy shinobi cctv") == "deep"


def test_resolve_effective_mode():
    # Explicit mode deep
    name, cfg = FleetCoordinator._resolve_effective_mode("deep", "cek ram")
    assert name == "deep"
    assert cfg["model"] == "gemini-3.8-flash-high"

    # Auto mode resolves light / summary to fast
    name, cfg = FleetCoordinator._resolve_effective_mode("auto", "cek ram")
    assert name == "fast"
    assert cfg["model"] == "gemini-3.8-flash-low"
    assert cfg["effort"] == "low"
    assert cfg["timeout_sec"] == 180

    name, cfg = FleetCoordinator._resolve_effective_mode("auto", "rangkum arsitektur sistem")
    assert name == "fast"
    assert cfg["model"] == "gemini-3.8-flash-low"

    # Auto mode resolves analytical discussion to balanced
    name, cfg = FleetCoordinator._resolve_effective_mode("auto", "analisa topologi jaringan")
    assert name == "balanced"
    assert cfg["model"] == "gemini-3.8-flash-medium"
    assert cfg["effort"] == "medium"
    assert cfg["timeout_sec"] == 300

    # Auto mode resolves pengerjaan / eksekusi to deep
    name, cfg = FleetCoordinator._resolve_effective_mode("auto", "eksekusi setup shinobi cctv")
    assert name == "deep"
    assert cfg["model"] == "gemini-3.8-flash-high"
    assert cfg["effort"] == "high"
    assert cfg["timeout_sec"] == 600


def test_reasoning_mode_persistence(tmp_path, monkeypatch):
    test_settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("orchestrator.coordinator.SETTINGS_DIR", tmp_path)
    monkeypatch.setattr("orchestrator.coordinator.SETTINGS_FILE", test_settings_file)

    # Initially defaults to auto
    assert FleetCoordinator._get_active_reasoning_mode() == "auto"

    # Set to fast
    assert FleetCoordinator._set_reasoning_mode("fast") is True
    assert FleetCoordinator._get_active_reasoning_mode() == "fast"

    # Set to balanced
    assert FleetCoordinator._set_reasoning_mode("balanced") is True
    assert FleetCoordinator._get_active_reasoning_mode() == "balanced"

    # Set to deep
    assert FleetCoordinator._set_reasoning_mode("deep") is True
    assert FleetCoordinator._get_active_reasoning_mode() == "deep"

    # Set to auto
    assert FleetCoordinator._set_reasoning_mode("auto") is True
    assert FleetCoordinator._get_active_reasoning_mode() == "auto"

    # Invalid mode returns False and doesn't overwrite
    assert FleetCoordinator._set_reasoning_mode("invalid_mode") is False
    assert FleetCoordinator._get_active_reasoning_mode() == "auto"


def test_mode_keyboard_and_text():
    kb = FleetCoordinator._build_mode_keyboard("auto")
    assert len(kb.inline_keyboard) == 3
    # Check auto has checked radio button in row 0
    assert "🔘" in kb.inline_keyboard[0][0].text
    assert "fleet_mode:auto" == kb.inline_keyboard[0][0].callback_data

    kb_deep = FleetCoordinator._build_mode_keyboard("deep")
    assert "🔘" in kb_deep.inline_keyboard[2][0].text
    assert "fleet_mode:deep" == kb_deep.inline_keyboard[2][0].callback_data

    txt = FleetCoordinator._format_mode_text("auto")
    assert "Auto (Smart" in txt
    assert "Deep Reasoning (High)" in txt
