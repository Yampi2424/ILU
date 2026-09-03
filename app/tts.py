"""
I.L.U. — Síntesis de voz (TTS) de la respuesta

Bloque Voz: da voz real a I.L.U. mediante motores neuronales. Sintetiza
el TEXTO de la respuesta de I.L.U. a audio (MP3) que el navegador
reproduce por Web Audio y visualiza en tiempo real.

Capa de proveedor (strategy): hoy edge-tts (voces neuronales de Microsoft
Edge, vía red, es-AR-ElenaNeural por defecto). Si el motor no está
disponible (sin red / sin paquete), la capa frontend cae al TTS nativo
del navegador (Web Speech). I.L.U. nunca se queda muda por fallo de un
único motor.

La voz SOLO reproduce la respuesta de I.L.U.; la entrada del usuario
sigue siendo texto (STT) por el MISMO /ask. La voz no es identidad ni
bypass de permisos.

Configuración por entorno:
  ILU_TTS_VOICE   voz (por defecto "es-AR-ElenaNeural")
  ILU_TTS_RATE    velocidad (por defecto "+0%")
"""

import asyncio
import os


class TTSUnavailable(Exception):
    """El motor de síntesis no está disponible o falló."""


class TTSService:
    """
    Servicio de síntesis de voz de I.L.U.

    `synthesize(texto)` devuelve bytes de audio (MP3) listos para
    reproducir. Lanza `TTSUnavailable` si el motor no responde; el
    frontend interpreta 503 y cae al TTS del navegador.
    """

    def __init__(self, voice=None, rate=None):
        self.name = "edge-tts"
        self.voice = voice or os.environ.get(
            "ILU_TTS_VOICE",
            "es-AR-ElenaNeural"
        )
        self.rate = rate or os.environ.get(
            "ILU_TTS_RATE",
            "+0%"
        )

    def synthesize(self, text, voice=None):
        """
        Sintetiza `text` a audio MP3.

        Devuelve `bytes`. Lanza `TTSUnavailable` si edge-tts no está
        instalado, falla la red, o la síntesis no produce audio.
        """
        text = (text or "").strip()
        voice = voice or self.voice

        if not text:
            raise TTSUnavailable("Texto vacío")

        try:
            import edge_tts
        except ImportError as error:
            raise TTSUnavailable(
                f"edge-tts no está disponible: {error}"
            )

        audio = bytearray()

        async def _run():
            nonlocal audio
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=self.rate,
            )
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])

        try:
            asyncio.run(_run())
        except Exception as error:  # noqa: BLE001 - se reporta como TTSUnavailable
            raise TTSUnavailable(str(error))

        if not audio:
            raise TTSUnavailable("La síntesis no produjo audio")

        return bytes(audio)
