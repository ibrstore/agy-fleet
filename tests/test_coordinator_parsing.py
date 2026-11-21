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
