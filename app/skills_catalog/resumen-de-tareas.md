---
name: resumen-de-tareas
description: >-
  Resume el estado actual del registro de tareas de I.L.U.: pendientes,
  en curso, completadas y próximas, en un mensaje claro.
variables:
  - estado_tareas
steps:
  - synthesize: true
    note: "Recogiendo el estado de mis tareas…"
  - tool: notify
    args:
      message: "Resumen de tareas listo."
    note: "Te aviso con el resumen."
---
# Skill: resumen-de-tareas

Plantilla de reportería sobre el registro de tareas. El orquestador
inyecta el estado agregado en `{estado_tareas}`; la skill lo convierte en
lenguaje natural y notifica. Pasos de solo lectura + notificación; no
ejecuta ni modifica tareas.