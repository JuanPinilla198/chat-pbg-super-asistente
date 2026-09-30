"""Acciones pendientes de confirmación y auditoría append-only."""

import time
import uuid
from dataclasses import dataclass, field


class ActionError(Exception):
    pass


@dataclass
class PendingAction:
    id: str
    agent_id: str
    tool: str
    payload: dict
    summary: str
    expires_at: float
    status: str = "pendiente"
    result: dict | None = field(default=None)

    def public(self) -> dict:
        return {
            "id": self.id,
            "tool": self.tool,
            "summary": self.summary,
            "status": self.status,
            "payload": self.payload,
        }


class PendingActions:
    def __init__(self, ttl_s: int, clock=time.time):
        self.ttl_s = ttl_s
        self.clock = clock
        self._items: dict[str, PendingAction] = {}

    def create(self, agent_id: str, tool: str, payload: dict, summary: str) -> PendingAction:
        action = PendingAction(
            id=uuid.uuid4().hex[:10],
            agent_id=agent_id,
            tool=tool,
            payload=payload,
            summary=summary,
            expires_at=self.clock() + self.ttl_s,
        )
        self._items[action.id] = action
        return action

    def take(self, agent_id: str, action_id: str) -> PendingAction:
        """Obtiene una acción pendiente del mismo agente; si no, falla sin revelar de quién es."""
        action = self._items.get(action_id)
        if action is None or action.agent_id != agent_id:
            raise ActionError("Acción no encontrada.")
        if action.status != "pendiente":
            raise ActionError(f"La acción ya está '{action.status}'.")
        if self.clock() > action.expires_at:
            action.status = "expirada"
            raise ActionError("La acción expiró; pídela de nuevo.")
        return action

    def pending_for(self, agent_id: str) -> list[dict]:
        return [
            a.public()
            for a in self._items.values()
            if a.agent_id == agent_id and a.status == "pendiente"
        ]


class AuditLog:
    """Append-only: no hay método para editar ni borrar."""

    def __init__(self):
        self._events: list[dict] = []

    def record(self, agent_id: str, tool: str, event: str, args, extra: dict | None = None):
        self._events.append(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "agent_id": agent_id,
                "tool": tool,
                "event": event,
                "args": args,
                **(extra or {}),
            }
        )

    def for_agent(self, agent_id: str) -> list[dict]:
        return [e for e in self._events if e["agent_id"] == agent_id]
