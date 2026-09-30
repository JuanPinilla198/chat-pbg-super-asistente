"""Reglas que decide el software, nunca el modelo.

- Qué permiso exige cada herramienta.
- Qué herramientas son de escritura y requieren confirmación (leído de data/mock_tools.json).
- No existe herramienta para enviar mensajes ni para confirmar acciones: el modelo no puede
  hacerlo aunque lo intente.
"""

import json
from pathlib import Path

from app.data import Agent

TOOL_PERMISSION = {
    "list_clients": "clients:read",
    "read_calendar": "calendar:read",
    "schedule_meeting": "calendar:write",
    "create_task": "tasks:write",
    "draft_message": "clients:read",
}


class Policy:
    def __init__(self, mock_tools_path: Path):
        spec = json.loads(mock_tools_path.read_text(encoding="utf-8"))["tools"]
        self.write = {t["name"]: t.get("write", False) for t in spec}
        self.confirm = {t["name"]: t.get("requires_confirmation", False) for t in spec}

    def is_known(self, tool: str) -> bool:
        return tool in TOOL_PERMISSION and tool in self.write

    def allowed(self, agent: Agent, tool: str) -> bool:
        return TOOL_PERMISSION[tool] in agent.permissions

    def needs_confirmation(self, tool: str) -> bool:
        # Toda escritura se confirma, aunque el archivo de configuración se equivoque.
        return self.write[tool] or self.confirm[tool]
