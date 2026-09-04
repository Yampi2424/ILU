"""
I.L.U. — Percepción (Bloque E: JARVIS Evolution).

I.L.U. debe VER, ESCUCHAR y COMPRENDER su entorno cuando tenga acceso a
sensores. Este módulo define una abstracción uniforme de percepción:

  - Un `SensorDriver` es una fuente de percepción con una capacidad
    declarada (system_state, filesystem, audio, camera, network...).
  - El `PerceptionHub` registra drivers y orquesta `perceive(capability)`.

Qué hay REAL hoy:
  - system_state: estado del sistema (uptime, memoria, cpu) — real y local.
  - filesystem:   cambio/estado de archivos dentro del workspace — real.

Qué queda como ARQUITECTURA LISTA (stub documentado, sin hardware/red):
  - audio, camera, network, proximity: el driver existe, declara su
    capacidad y devuelve "unavailable" con motivo claro. Cuando exista
    el sensor/API, solo hay que implementar su `sample()`.

La percepción es SOLO lectura del entorno: jamás ejecuta acciones ni
concede permisos. Las acciones sobre el mundo viven en `integrations.py`
y pasan por la compuerta de seguridad.
"""

import os
import shutil
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
# Drivers de arquitectura lista (stub documentado)
# ----------------------------------------------------------------------

class StubSensorDriver(SensorDriver):
    """
    Base para sensores cuya fuente (hardware/API) aún no está disponible.

    Implementar `sample()` cuando el sensor exista; mientras tanto
    `available()` devuelve False y `perceive()` reporta con motivo.
    """

    capability = "stub"
    reason = "hardware_or_api_unavailable"

    def available(self):
        return False


class AudioDriver(StubSensorDriver):
    """Microfón/escucha ambiental. La entrada de voz real vive en el
    frontend (Web Speech); un driver de audio pasivo en el backend queda
    PLANIFICADO (requiere acceso al dispositivo de audio del servidor)."""
    capability = "audio"


class CameraDriver(StubSensorDriver):
    """Visión por cámara. PLANIFICADO: requiere cámara y un modelo de
    visión. Deja el punto de integración listo."""
    capability = "camera"


class NetworkDriver(StubSensorDriver):
    """Percepción de red/dispositivos en red. PLANIFICADO."""
    capability = "network"


class ProximityDriver(StubSensorDriver):
    """Presencia física cercana. PLANIFICADO (beacon/BLE/RFID)."""
    capability = "proximity"


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
    Hub por defecto de I.L.U.: registra los drivers reales y los stubs
    de arquitectura lista.
    """
    hub = PerceptionHub()

    hub.register(SystemStateDriver())
    hub.register(FilesystemDriver())
    hub.register(AudioDriver())
    hub.register(CameraDriver())
    hub.register(NetworkDriver())
    hub.register(ProximityDriver())

    return hub
