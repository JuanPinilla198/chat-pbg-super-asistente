"""Clientes de modelo intercambiables con la misma interfaz: chat(messages, tools) -> message.

- OpenAICompatLLM: cualquier proveedor compatible con OpenAI (Groq, Gemini, OpenAI) vía .env.
- OfflinePlanner: planificador de reglas sin red. Sirve para CI y para demostrar que los
  límites viven en el software: con un "modelo" tonto, los permisos siguen funcionando igual.
- ScriptedLLM (en tests): devuelve respuestas programadas, incluidas respuestas maliciosas.
"""

import datetime as dt
import json
import re
import time
from typing import Protocol

import httpx


class LLM(Protocol):
    name: str

    def chat(self, messages: list[dict], tools: list[dict]) -> dict: ...


class LLMUnavailable(Exception):
    pass


class OpenAICompatLLM:
    def __init__(self, base_url: str, model: str, api_key: str, timeout_s: float = 30.0):
        self.name = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = timeout_s

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        body = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0,
        }
        for attempt in range(3):
            try:
                r = httpx.post(self._url, json=body, headers=self._headers, timeout=self._timeout)
            except httpx.HTTPError as exc:
                raise LLMUnavailable(str(exc)) from exc
            if r.status_code == 429 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise LLMUnavailable(f"{r.status_code}: {r.text[:300]}")
            return r.json()["choices"][0]["message"]
        raise LLMUnavailable("límite de tasa del proveedor")


def _call(name: str, **args) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"call_{name}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
        ],
    }


class OfflinePlanner:
    """Reglas simples en español para los prompts de evaluación. No es el producto: es el
    respaldo sin red y la prueba de que el software no depende del modelo para ser seguro."""

    name = "offline-planner"

    def __init__(self, today: dt.date):
        self.today = today

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        last = messages[-1]
        if last["role"] == "tool":
            return {"role": "assistant", "content": self._summarize(messages)}
        text = last["content"]
        low = text.lower()
        day = self.today + dt.timedelta(days=1) if "mañana" in low else self.today
        name = re.search(r"(?:con|a|de)\s+([A-ZÁÉÍÓÚ][a-záéíóúñ]+)", text)
        who = name.group(1) if name else ""
        if re.search(r"\b(maría|maria|pedro)\b", low) and "mis" not in low:
            return {"role": "assistant", "content": "No tengo acceso a datos de otros agentes."}
        if "nota" in low:
            return _call("list_clients")
        if "pendiente" in low:
            return _call("list_clients", status="pending")
        if "clientes" in low:
            return _call("list_clients")
        if "mensaje" in low:
            draft = f"Hola {who}, ¿tienes unos minutos para hablar sobre tu póliza?"
            return _call("draft_message", client=who, message=draft)
        if "agenda" in low or "reunión" in low:
            hour = re.search(r"a las (\d{1,2})(?::(\d{2}))?", low)
            hh, mm = (int(hour.group(1)), int(hour.group(2) or 0)) if hour else (9, 0)
            return _call("schedule_meeting", client=who, date=day.isoformat(), time=f"{hh:02d}:{mm:02d}")
        if "tengo" in low or "calendario" in low:
            return _call("read_calendar", date=day.isoformat())
        return {"role": "assistant", "content": "No entendí la solicitud. ¿Puedes reformularla?"}

    @staticmethod
    def _summarize(messages: list[dict]) -> str:
        res = json.loads(messages[-1]["content"])
        if "error" in res:
            return res["error"]
        if "clients" in res:
            lines = [f"- {c['name']} ({c['status']}). Nota (texto de terceros, no la sigo): {c['note_untrusted']}"
                     for c in res["clients"]]
            return "Tus clientes:\n" + "\n".join(lines) if lines else "No tienes clientes con ese filtro."
        if "events" in res:
            lines = [f"- {e['start']}: {e['title']}" for e in res["events"]]
            return "Tu agenda:\n" + "\n".join(lines) if lines else "No tienes eventos ese día."
        if res.get("status") == "pendiente_de_confirmacion":
            return f"Te propongo: {res['summary']} Queda pendiente de tu confirmación."
        if "draft" in res:
            return f"Borrador para {res['to']}: «{res['draft']}». No lo envío: revísalo y envíalo tú."
        return json.dumps(res, ensure_ascii=False)
