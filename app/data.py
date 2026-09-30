"""Datos sintéticos en memoria.

Regla de aislamiento: el resto de la aplicación nunca toca `Store` directamente; solo recibe un
`ScopedStore` construido con el agente autenticado. Ningún método acepta un agent_id, así que ni
el modelo ni una herramienta mal escrita pueden pedir datos de otro agente.
"""

import json
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_OFFSET = timezone(timedelta(hours=-5))  # Colombia, si el agente no tiene eventos


def _norm(text: str) -> str:
    """Minúsculas y sin tildes: 'José' encuentra a 'Jose Martinez'."""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


class NotFound(Exception):
    pass


class Ambiguous(Exception):
    pass


@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    role: str
    permissions: frozenset[str]


class Store:
    def __init__(self, agents: list[dict], clients: list[dict], calendar: list[dict]):
        self.agents = {
            a["id"]: Agent(a["id"], a["name"], a["role"], frozenset(a["permissions"]))
            for a in agents
        }
        self.clients = clients
        self.calendar = calendar
        self.tasks: list[dict] = []

    @classmethod
    def from_dir(cls, path: Path) -> "Store":
        def load(name: str):
            return json.loads((path / name).read_text(encoding="utf-8"))

        return cls(load("agents.json"), load("clients.json"), load("calendar.json"))

    def agent(self, agent_id: str) -> Agent:
        if agent_id not in self.agents:
            raise NotFound("agente desconocido")
        return self.agents[agent_id]


class ScopedStore:
    def __init__(self, store: Store, agent: Agent):
        self._store = store
        self.agent = agent

    @property
    def tz(self) -> timezone:
        """Zona horaria del agente, inferida de su propio calendario."""
        for ev in self._store.calendar:
            if ev["agent_id"] == self.agent.id:
                return datetime.fromisoformat(ev["start"]).tzinfo or DEFAULT_OFFSET
        return DEFAULT_OFFSET

    def clients(self, status: str | None = None) -> list[dict]:
        return [
            c
            for c in self._store.clients
            if c["agent_id"] == self.agent.id and (status is None or c["status"] == status)
        ]

    def find_client(self, ref: str) -> dict:
        """Resuelve por id o nombre, solo dentro de la cartera del agente.

        Un cliente de otro agente produce el mismo error que uno inexistente: no se filtra
        ni siquiera su existencia.
        """
        ref_norm = _norm(ref)
        own = self.clients()
        exact = [c for c in own if _norm(c["id"]) == ref_norm or _norm(c["name"]) == ref_norm]
        matches = exact or [c for c in own if ref_norm and ref_norm in _norm(c["name"])]
        if not matches:
            raise NotFound(f"No encontré a '{ref}' entre tus clientes.")
        if len(matches) > 1:
            names = ", ".join(c["name"] for c in matches)
            raise Ambiguous(f"'{ref}' coincide con varios clientes: {names}.")
        return matches[0]

    def events(self, day: date | None = None) -> list[dict]:
        own = [ev for ev in self._store.calendar if ev["agent_id"] == self.agent.id]
        if day is None:
            return own
        return [ev for ev in own if datetime.fromisoformat(ev["start"]).date() == day]

    def add_event(self, start: datetime, minutes: int, title: str, client_id: str) -> dict:
        ev = {
            "agent_id": self.agent.id,
            "start": start.isoformat(),
            "minutes": minutes,
            "title": title,
            "client_id": client_id,
        }
        self._store.calendar.append(ev)
        return ev

    def add_task(self, title: str, due: date | None, client_id: str | None) -> dict:
        task = {
            "id": f"T{uuid.uuid4().hex[:6]}",
            "agent_id": self.agent.id,
            "title": title,
            "due_date": due.isoformat() if due else None,
            "client_id": client_id,
        }
        self._store.tasks.append(task)
        return task
