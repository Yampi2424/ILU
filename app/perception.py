"""
I.L.U. — Percepción (Bloque E: JARVIS Evolution).

I.L.U. debe VER, ESCUCHAR y COMPRENDER su entorno. Este módulo define una
abstracción uniforme de percepción:

  - Un `SensorDriver` es una fuente de percepción con una capacidad
    declarada (system_state, filesystem, audio, camera, network...).
  - El `PerceptionHub` registra drivers y orquesta `perceive(capability)`.

Qué hay REAL hoy (sensado local, sin fricción):
  - system_state: estado del sistema (uptime, plataforma, hostname) — real.
  - filesystem:   estado de archivos dentro del workspace — real.
  - network:      interfaces, gateway, conectividad y conexiones activas
                  (psutil + /proc/net/route) — real y local.
  - proximity:    presencia humana (sesiones activas con psutil.users +
                  dispositivos del LAN vía /proc/net/arp) — real y local.

Hardware-aware (detección REAL del hardware; captura opcional):
  - audio:  enumera los micrófonos/captura reales del sistema (ALSA) y,
            si hay un backend de captura importable (sounddevice), mide el
            nivel ambiental. Sin backend, reporta el hardware y el motivo.
  - camera: enumera las cámaras reales (V4L2 /dev/video*) y, si hay un
            backend de captura (OpenCV), captura un frame y reporta su
            resolución. Sin backend, reporta el hardware y el motivo.

La percepción es SOLO lectura del entorno: jamás ejecuta acciones ni
concede permisos. Las acciones sobre el mundo viven en `integrations.py`
y pasan por la compuerta de seguridad.
"""

import os
import socket
import time


# ----------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------

class SensorDriver:
    """Base de un driver de percepción."""

    capability = "base"

    def available(self):
        return False

    def sample(self):
        raise NotImplementedError

    def perceive(self):
        if not self.available():
            return {
                "capability": self.capability,
                "available": False,
                "reason": getattr(self, "reason", "sensor_unavailable"),
                "data": None,
            }

        try:
            return {
                "capability": self.capability,
                "available": True,
                "data": self.sample(),
            }
        except Exception as error:
            return {
                "capability": self.capability,
                "available": False,
                "reason": str(error),
                "data": None,
            }


# ----------------------------------------------------------------------
# Drivers reales (locales)
# ----------------------------------------------------------------------

class SystemStateDriver(SensorDriver):
    """Estado del sistema local: real y sin dependencias externas."""

    capability = "system_state"

    def available(self):
        return True

    def sample(self):
        uptime = time.time() - _boot_time()

        return {
            "uptime_seconds": round(uptime),
            "platform": os.uname().sysname,
            "hostname": os.uname().nodename,
        }


class FilesystemDriver(SensorDriver):
    """Estado de archivos del workspace: real y acotado."""

    capability = "filesystem"

    def __init__(self, workspace=None):
        self.workspace = workspace or os.environ.get(
            "ILU_WORKSPACE",
            os.getcwd(),
        )

    def available(self):
        return os.path.isdir(self.workspace)

    def sample(self):
        try:
            entries = sorted(os.listdir(self.workspace))[:50]
        except OSError:
            entries = []

        return {
            "workspace": self.workspace,
            "entries": entries,
            "count": len(entries),
        }


# ----------------------------------------------------------------------
# Drivers reales de red y presencia
# ----------------------------------------------------------------------

def _default_gateway():
    """Gateway por defecto desde /proc/net/route (sin dependencias)."""
    import struct
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    gateway = socket.inet_ntoa(
                        struct.pack("<L", int(parts[2], 16))
                    )
                    return {"gateway": gateway, "interface": parts[0]}
    except (OSError, ValueError, struct.error):
        pass
    return None


class NetworkDriver(SensorDriver):
    """Percepción de red local: interfaces, gateway, conectividad y
    conexiones activas. Real, local y sin dependencias externas."""

    capability = "network"
    reason = "network_unavailable"

    def available(self):
        return True

    def sample(self):
        import psutil

        interfaces = {}
        for name, addrs in psutil.net_if_addrs().items():
            if name in ("lo", "docker0"):
                continue
            ipv4 = []
            ipv6 = []
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address:
                    ipv4.append(addr.address)
                elif addr.family == socket.AF_INET6 and addr.address:
                    ipv6.append(addr.address.split("%")[0])
            if ipv4 or ipv6:
                interfaces[name] = {
                    "ipv4": ipv4,
                    "ipv6": ipv6,
                }

        gateway = _default_gateway()

        # Conectividad: prueba TCP breve hacia el gateway (si lo hay) o
        # un host estable. Nunca bloquea más de 1.5s en total.
        connectivity = "offline"
        try:
            probe = gateway["gateway"] if gateway else "1.1.1.1"
            sock = socket.create_connection((probe, 53), timeout=1.2)
            sock.close()
            connectivity = "online"
        except OSError:
            connectivity = "offline"

        # Conexiones activas de la máquina (solo conteos por estado).
        connections = {}
        try:
            for conn in psutil.net_connections(kind="inet"):
                status = conn.status or "unknown"
                connections[status] = connections.get(status, 0) + 1
        except (psutil.AccessDenied, OSError):
            connections = {}

        return {
            "interfaces": interfaces,
            "gateway": gateway,
            "connectivity": connectivity,
            "active_connections": connections,
        }


class ProximityDriver(SensorDriver):
    """Presencia humana cercana: sesiones activas del sistema + otros
    dispositivos en el LAN (tabla ARP). Real y local."""

    capability = "proximity"
    reason = "no_human_presence_detected"

    def available(self):
        return True

    def _arp_table(self):
        """Dispositivos del LAN visibles vía /proc/net/arp (sin root)."""
        devices = []
        try:
            with open("/proc/net/arp", "r", encoding="utf-8") as handle:
                next(handle, None)  # cabecera
                for line in handle:
                    parts = line.split()
                    if (
                        len(parts) >= 6
                        and parts[1] == "0x1"
                        and parts[3] != "00:00:00:00:00:00"
                    ):
                        devices.append({
                            "ip": parts[0],
                            "mac": parts[3],
                            "interface": parts[5],
                        })
        except (OSError, IndexError):
            pass
        return devices

    def sample(self):
        import psutil

        humans = []
        try:
            for user in psutil.users():
                humans.append({
                    "user": user.name,
                    "terminal": user.terminal,
                    "host": user.host,
                    "started": user.started,
                })
        except (psutil.Error, OSError):
            humans = []

        devices = self._arp_table()

        return {
            "humans_logged_in": humans,
            "human_count": len(humans),
            "lan_devices": devices,
            "lan_device_count": len(devices),
        }


# ----------------------------------------------------------------------
# Drivers hardware-aware (detección real + captura opcional)
# ----------------------------------------------------------------------

class AudioDriver(SensorDriver):
    """Escucha ambiental. Detecta los micrófonos/captura REALES del
    sistema (ALSA). Si hay un backend de captura importable (sounddevice),
    mide el nivel ambiental; si no, reporta el hardware detectado con un
    motivo honesto."""

    capability = "audio"
    reason = "no_capture_device"

    def _capture_devices(self):
        """Dispositivos de captura de audio reales (ALSA).

        Lee /proc/asound/pcm, que lista todos los PCM en líneas como
        "00-01: Headset (*) :  : playback 1 : capture 1". Solo los que
        declaran "capture 1" son dispositivos de escucha reales.
        """
        devices = []
        try:
            with open("/proc/asound/pcm", "r", encoding="utf-8") as handle:
                for line in handle:
                    if ":" not in line or "capture" not in line.lower():
                        continue
                    left, right = line.split(":", 1)
                    pcm_id = left.strip()
                    description = right.strip()
                    if "capture 1" not in description:
                        continue
                    devices.append({
                        "pcm": pcm_id,
                        "description": description,
                    })
        except OSError:
            pass
        return devices

    def available(self):
        return len(self._capture_devices()) > 0

    def sample(self):
        devices = self._capture_devices()

        backend = None
        ambient_level = None

        # Captura real del nivel ambiental si hay backend disponible.
        try:
            import sounddevice as sd
            import numpy as np
            backend = "sounddevice"
            duration = 0.4
            frames = int(44100 * duration)
            recorded = sd.rec(frames, samplerate=44100, channels=1,
                              dtype="float32")
            sd.wait()
            if recorded is not None and recorded.size:
                rms = float(np.sqrt(np.mean(np.square(recorded))))
                ambient_level = round(rms, 4)
        except Exception:
            backend = None

        return {
            "microphones": devices,
            "capture_backend": backend,
            "ambient_level": ambient_level,
            "capturing": backend is not None and ambient_level is not None,
        }


class CameraDriver(SensorDriver):
    """Visión por cámara. Detecta las cámaras REALES (V4L2 /dev/video*).
    Si hay un backend de captura (OpenCV), captura un frame y reporta su
    resolución; si no, reporta el hardware detectado con un motivo."""

    capability = "camera"
    reason = "no_camera_device"

    def _video_devices(self):
        """Cámaras reales: /dev/video* + nombre del driver V4L2."""
        devices = []
        try:
            import glob
            for path in sorted(glob.glob("/dev/video*")):
                idx = path.rsplit("video", 1)[-1]
                name = ""
                try:
                    with open(
                        f"/sys/class/video4linux/video{idx}/name",
                        "r",
                        encoding="utf-8",
                    ) as handle:
                        name = handle.read().strip()
                except OSError:
                    pass
                devices.append({"device": path, "name": name})
        except OSError:
            pass
        return devices

    def available(self):
        return len(self._video_devices()) > 0

    def sample(self):
        devices = self._video_devices()

        backend = None
        frame = None

        # Captura real de un frame si hay backend disponible.
        try:
            import cv2
            backend = "opencv"
            cap = cv2.VideoCapture(0)
            ok, img = cap.read()
            cap.release()
            if ok and img is not None:
                height, width = img.shape[:2]
                frame = {
                    "width": int(width),
                    "height": int(height),
                    "captured": True,
                }
        except Exception:
            backend = None

        return {
            "cameras": devices,
            "capture_backend": backend,
            "frame": frame,
        }


# ----------------------------------------------------------------------
# Hub
# ----------------------------------------------------------------------

def _boot_time():
    try:
        with open("/proc/stat", "r") as handle:
            for line in handle:
                if line.startswith("btime"):
                    return float(line.split()[1])
    except (OSError, IndexError, ValueError):
        pass

    return time.time()


class PerceptionHub:
    """
    Orquesta los sensores de I.L.U.

      - register(driver): añade un sensor.
      - perceive(capability): consulta UN sensor por su capacidad.
      - perceive_all(): consulta todos y agrega el resultado.
      - list_capabilities(): sensores conocidos y si están disponibles.
    """

    def __init__(self):
        self.drivers = {}

    def register(self, driver):
        if isinstance(driver, SensorDriver):
            self.drivers[driver.capability] = driver
        return self

    def perceive(self, capability):
        driver = self.drivers.get(capability)

        if driver is None:
            return {
                "capability": capability,
                "available": False,
                "reason": "no_driver_registered",
                "data": None,
            }

        return driver.perceive()

    def perceive_all(self):
        return {
            capability: driver.perceive()
            for capability, driver in self.drivers.items()
        }

    def list_capabilities(self):
        return [
            {
                "capability": driver.capability,
                "available": driver.available(),
            }
            for driver in self.drivers.values()
        ]


def create_perception_hub():
    """
    Hub por defecto de I.L.U.: registra los drivers reales de percepción
    (sistema, archivos, red, presencia) y los hardware-aware (audio,
    cámara, con detección real + captura opcional).
    """
    hub = PerceptionHub()

    hub.register(SystemStateDriver())
    hub.register(FilesystemDriver())
    hub.register(AudioDriver())
    hub.register(CameraDriver())
    hub.register(NetworkDriver())
    hub.register(ProximityDriver())

    return hub
