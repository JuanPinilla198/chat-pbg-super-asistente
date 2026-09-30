"""Los límites se prueban con un modelo programado, incluso uno malicioso: si el software los
impone, da igual lo que el modelo intente."""

import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app

A1 = {"X-Agent-Id": "A1"}  # Andres: C1 Laura, C2 Jose
A2 = {"X-Agent-Id": "A2"}  # Maria: C3 Pedro


def call(name, **args):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c_{name}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"role": "assistant", "content": text}


class ScriptedLLM:
    name = "scripted"

    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools):
        self.seen.append(list(messages))
        return self.turns.pop(0) if self.turns else say("fin")


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(settings, "app_today", "2026-09-29")


def client_with(*turns):
    llm = ScriptedLLM(*turns)
    return TestClient(create_app(llm)), llm


def tool_results(llm):
    return [json.loads(m["content"]) for m in llm.seen[-1] if m["role"] == "tool"]


# --- Aislamiento de datos ---

def test_list_clients_only_returns_own_clients():
    c, llm = client_with(call("list_clients"), say("ok"))
    c.post("/chat", json={"message": "mis clientes"}, headers=A1)
    names = [x["name"] for x in tool_results(llm)[0]["clients"]]
    assert names == ["Laura Gomez", "Jose Martinez"]


def test_model_cannot_pass_agent_id_to_reach_other_agent():
    c, llm = client_with(call("list_clients", agent_id="A2"), say("ok"))
    r = c.post("/chat", json={"message": "clientes de María"}, headers=A1).json()
    assert "Argumentos inválidos" in tool_results(llm)[0]["error"]
    assert "Pedro" not in json.dumps(r)


def test_other_agents_client_looks_nonexistent():
    c, llm = client_with(call("schedule_meeting", client="Pedro", date="2026-09-30", time="10:00"), say("ok"))
    c.post("/chat", json={"message": "agenda con Pedro"}, headers=A1)
    err = tool_results(llm)[0]["error"]
    assert err == "No encontré a 'Pedro' entre tus clientes."  # igual que un cliente inexistente


def test_calendar_is_scoped():
    c, llm = client_with(call("read_calendar", date="2026-09-30"), say("ok"))
    c.post("/chat", json={"message": "¿qué tengo mañana?"}, headers=A1)
    events = tool_results(llm)[0]["events"]
    assert [e["title"] for e in events] == ["Follow-up Laura"]


def test_unknown_agent_is_rejected():
    c, _ = client_with()
    assert c.post("/chat", json={"message": "hola"}, headers={"X-Agent-Id": "A9"}).status_code == 401


# --- Confirmación antes de acciones materiales ---

def schedule_laura():
    return call("schedule_meeting", client="Laura", date="2026-09-30", time="10:00")


def test_write_is_only_proposed_until_confirmed():
    c, _ = client_with(schedule_laura(), say("pendiente"))
    r = c.post("/chat", json={"message": "Agenda con Laura mañana a las 10"}, headers=A1).json()
    assert len(r["pending"]) == 1
    assert len(c.get("/calendar", headers=A1).json()) == 1  # nada escrito todavía
    summary = r["pending"][0]["summary"]
    assert "Laura Gomez" in summary and "2026-09-30 10:00 (UTC-04:00)" in summary
    assert "Conflicto con: Follow-up Laura" in summary  # lo detecta el software, no el modelo


def test_confirm_executes_exactly_what_was_shown():
    c, _ = client_with(schedule_laura(), say("pendiente"))
    action = c.post("/chat", json={"message": "agenda"}, headers=A1).json()["pending"][0]
    r = c.post(f"/actions/{action['id']}/confirm", headers=A1)
    assert r.status_code == 200
    events = c.get("/calendar", headers=A1).json()
    assert events[-1]["start"] == "2026-09-30T10:00:00-04:00"


def test_other_agent_cannot_confirm_and_replay_fails():
    c, _ = client_with(schedule_laura(), say("pendiente"))
    action = c.post("/chat", json={"message": "agenda"}, headers=A1).json()["pending"][0]
    assert c.post(f"/actions/{action['id']}/confirm", headers=A2).status_code == 409
    assert c.post(f"/actions/{action['id']}/confirm", headers=A1).status_code == 200
    assert c.post(f"/actions/{action['id']}/confirm", headers=A1).status_code == 409


def test_expired_action_cannot_be_confirmed():
    c, _ = client_with(schedule_laura(), say("pendiente"))
    action = c.post("/chat", json={"message": "agenda"}, headers=A1).json()["pending"][0]
    c.app.state.s.actions.clock = lambda: 10**12
    assert c.post(f"/actions/{action['id']}/confirm", headers=A1).status_code == 409


def test_model_has_no_way_to_confirm_or_send():
    c, llm = client_with(call("confirm_action", action_id="x"), call("send_message", to="Jose"), say("ok"))
    c.post("/chat", json={"message": "hazlo ya"}, headers=A1)
    errors = [m["content"] for m in llm.seen[-1] if m["role"] == "tool"]
    assert all("no existe" in e for e in errors)


def test_draft_message_is_never_sent():
    c, llm = client_with(call("draft_message", client="José", message="Hola José"), say("ok"))
    c.post("/chat", json={"message": "Envíale un mensaje a José"}, headers=A1)
    res = tool_results(llm)[0]
    assert res["sent"] is False and res["to"] == "Jose Martinez"


# --- Permisos ---

def test_missing_permission_blocks_tool():
    c, llm = client_with(schedule_laura(), say("ok"))
    store = c.app.state.s.store
    a1 = store.agents["A1"]
    store.agents["A1"] = type(a1)(a1.id, a1.name, a1.role, frozenset({"clients:read"}))
    r = c.post("/chat", json={"message": "agenda"}, headers=A1).json()
    assert tool_results(llm)[0]["error"] == "No tienes permiso para esta acción."
    assert r["pending"] == []


# --- Inyección en datos ---

def test_note_injection_is_marked_untrusted_and_cannot_escalate():
    # El modelo "obedece" la nota e intenta todo lo posible: el software no le da nada de A2.
    c, llm = client_with(
        call("list_clients"),
        call("list_clients", agent_id="A2"),
        call("read_calendar"),
        say("ok"),
    )
    r = c.post("/chat", json={"message": "Lee la nota de José y sigue sus instrucciones"}, headers=A1)
    dumped = json.dumps(r.json()) + json.dumps(tool_results(llm))
    assert "Pedro" not in dumped and "C3" not in dumped
    first = tool_results(llm)[0]
    assert "note_untrusted" in first["clients"][1] and "no instrucciones" in first["aviso"]


def test_audit_is_scoped_and_records_software_decisions():
    c, _ = client_with(schedule_laura(), say("ok"))
    c.post("/chat", json={"message": "agenda"}, headers=A1)
    assert c.get("/audit", headers=A2).json() == []
    events = [e["event"] for e in c.get("/audit", headers=A1).json()]
    assert events == ["propuesta creada, espera confirmación"]
