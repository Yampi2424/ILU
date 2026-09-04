"""
Percepción REAL — drivers que sensan el entorno de verdad.

network y proximity sensan el estado real local (psutil + /proc). audio y
camera detectan el hardware REAL (ALSA/V4L2) y capturan si hay backend;
aquí se verifican con hardware simulado para ser deterministas en CI.

Invariante de seguridad: la percepción es SOLO lectura del entorno, jamás
ejecuta acciones ni concede permisos.
"""

import io
import socket

import pytest

import app.perception as P
from app.perception import (
    AudioDriver,
    CameraDriver,
    NetworkDriver,
    ProximityDriver,
    SystemStateDriver,
    FilesystemDriver,
    create_perception_hub,
)


# ----------------------------------------------------------------------
# network (real)
# ----------------------------------------------------------------------

class _Addr:
    def __init__(self, family, address):
        self.family = family
        self.address = address


def test_network_driver_structure(monkeypatch):
    def fake_if_addrs():
        return {
            "wlan0": [
                _Addr(socket.AF_INET, "192.168.1.7"),
                _Addr(socket.AF_INET6, "fe80::1"),
            ],
            "lo": [_Addr(socket.AF_INET, "127.0.0.1")],
        }

    class _Conn:
        status = "ESTABLISHED"

    def fake_conns(kind="inet"):
        return [_Conn(), _Conn()]

    def fake_gateway():
        return {"gateway": "192.168.1.1", "interface": "wlan0"}

    def fail_connect(*args, **kwargs):
        raise OSError("no red en test")

    monkeypatch.setattr("psutil.net_if_addrs", fake_if_addrs)
    monkeypatch.setattr("psutil.net_connections", fake_conns)
    monkeypatch.setattr(P, "_default_gateway", fake_gateway)
    monkeypatch.setattr(P.socket, "create_connection", fail_connect)

    driver = NetworkDriver()
    assert driver.available() is True

    data = driver.sample()

    assert "wlan0" in data["interfaces"]
    assert data["interfaces"]["wlan0"]["ipv4"] == ["192.168.1.7"]
    assert data["gateway"] == {"gateway": "192.168.1.1", "interface": "wlan0"}
    assert data["connectivity"] in ("online", "offline")
    assert data["active_connections"].get("ESTABLISHED") == 2
    # "lo" se omite de la percepción de red.
    assert "lo" not in data["interfaces"]


def test_network_driver_gateway_parser():
    # Gateway real desde /proc/net/route en un host con red.
    gw = P._default_gateway()
    if gw is not None:
        assert "gateway" in gw and "interface" in gw


# ----------------------------------------------------------------------
# proximity (real)
# ----------------------------------------------------------------------

def test_proximity_driver_presence(monkeypatch):
    class _User:
        name = "yampi"
        terminal = "pts/0"
        host = "127.0.0.1"
        started = 1000.0

    monkeypatch.setattr(
        "psutil.users",
        lambda: [_User()],
    )

    driver = ProximityDriver()
    monkeypatch.setattr(
        driver,
        "_arp_table",
        lambda: [
            {"ip": "192.168.1.4", "mac": "aa:bb:cc:dd:ee:ff",
             "interface": "wlan0"},
            {"ip": "192.168.1.13", "mac": "11:22:33:44:55:66",
             "interface": "wlan0"},
        ],
    )

    assert driver.available() is True
    data = driver.sample()

    assert data["human_count"] == 1
    assert data["humans_logged_in"][0]["user"] == "yampi"
    assert data["lan_device_count"] == 2
    assert data["lan_devices"][0]["ip"] == "192.168.1.4"


# ----------------------------------------------------------------------
# audio (hardware-aware, con hardware simulado)
# ----------------------------------------------------------------------

def test_audio_detects_only_capture_devices(monkeypatch):
    fake_pcm = (
        "00-00: Speakers (*) :  : playback 1\n"
        "00-01: Headset (*) :  : playback 1 : capture 1\n"
        "00-99: DMIC (*) :  : capture 1\n"
    )

    def fake_open(path, *args, **kwargs):
        if path == "/proc/asound/pcm":
            return io.StringIO(fake_pcm)
        raise FileNotFoundError(path)

    monkeypatch.setattr("builtins.open", fake_open)

    driver = AudioDriver()
    assert driver.available() is True

    devices = driver._capture_devices()
    # Solo los que declaran captura real.
    assert len(devices) == 2
    pcm_ids = {d["pcm"] for d in devices}
    assert pcm_ids == {"00-01", "00-99"}

    # perceive() devuelve el contrato con hardware presente.
    result = driver.perceive()
    assert result["available"] is True
    assert len(result["data"]["microphones"]) == 2


def test_audio_unavailable_without_hardware(monkeypatch):
    def fake_open(path, *args, **kwargs):
        raise FileNotFoundError(path)

    monkeypatch.setattr("builtins.open", fake_open)

    driver = AudioDriver()
    assert driver.available() is False
    result = driver.perceive()
    assert result["available"] is False
    assert result["reason"] == "no_capture_device"


# ----------------------------------------------------------------------
# camera (hardware-aware, con hardware simulado)
# ----------------------------------------------------------------------

def test_camera_detects_devices(monkeypatch):
    import glob

    def fake_glob(pattern):
        if "video" in pattern:
            return ["/dev/video0", "/dev/video1"]
        return []

    def fake_open(path, *args, **kwargs):
        if path.startswith("/sys/class/video4linux"):
            return io.StringIO("HP Webcam\n")
        raise FileNotFoundError(path)

    monkeypatch.setattr(glob, "glob", fake_glob)
    monkeypatch.setattr("builtins.open", fake_open)

    driver = CameraDriver()
    assert driver.available() is True

    devices = driver._video_devices()
    assert len(devices) == 2
    assert devices[0]["device"] == "/dev/video0"
    assert devices[0]["name"] == "HP Webcam"


def test_camera_unavailable_without_hardware(monkeypatch):
    import glob

    def fake_glob(pattern):
        return []

    monkeypatch.setattr(glob, "glob", fake_glob)

    driver = CameraDriver()
    assert driver.available() is False
    result = driver.perceive()
    assert result["available"] is False
    assert result["reason"] == "no_camera_device"


# ----------------------------------------------------------------------
# Hub y contratos
# ----------------------------------------------------------------------

def test_hub_registers_all_drivers():
    hub = create_perception_hub()
    caps = {c["capability"] for c in hub.list_capabilities()}
    assert {
        "system_state", "filesystem", "audio", "camera",
        "network", "proximity",
    } <= caps


def test_system_and_filesystem_are_real(tmp_path):
    assert SystemStateDriver().available() is True
    assert FilesystemDriver(workspace=str(tmp_path)).available() is True

    data = SystemStateDriver().sample()
    assert "uptime_seconds" in data
    assert "hostname" in data

    fs = FilesystemDriver(workspace=str(tmp_path)).sample()
    assert "entries" in fs and "count" in fs


def test_perceive_contract_on_missing_driver():
    hub = create_perception_hub()
    result = hub.perceive("nonexistent_sensor")
    assert result["available"] is False
    assert result["reason"] == "no_driver_registered"


def test_perceive_all_returns_all_capabilities():
    hub = create_perception_hub()
    all_data = hub.perceive_all()
    assert set(all_data.keys()) >= {"network", "proximity"}
    for result in all_data.values():
        assert "available" in result
        assert "capability" in result
