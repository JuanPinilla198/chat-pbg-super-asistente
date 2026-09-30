import datetime as dt
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import assistant
from app.actions import ActionError, AuditLog, PendingActions
from app.config import settings
from app.data import NotFound, ScopedStore, Store
from app.llm import LLM, OfflinePlanner, OpenAICompatLLM
from app.policy import Policy
from app.tools import ToolRunner, execute_confirmed

STATIC = Path(__file__).parent / "static"


class State:
    def __init__(self, llm: LLM | None = None):
        self.store = Store.from_dir(settings.data_dir)
        self.policy = Policy(settings.data_dir / "mock_tools.json")
        self.actions = PendingActions(settings.confirmation_ttl_s)
        self.audit = AuditLog()
        self.llm = llm or self._default_llm()

    def today(self) -> dt.date:
        return dt.date.fromisoformat(settings.app_today) if settings.app_today else dt.date.today()

    def _default_llm(self) -> LLM:
        base_url, model, key = settings.resolved_llm()
        if key:
            return OpenAICompatLLM(base_url, model, key, settings.llm_timeout_s)
        return OfflinePlanner(self.today())

    def scoped(self, agent_id: str) -> ScopedStore:
        try:
            return ScopedStore(self.store, self.store.agent(agent_id))
        except NotFound as exc:
            raise HTTPException(401, "Agente no autenticado.") from exc

    def runner(self, agent_id: str) -> ToolRunner:
        return ToolRunner(self.scoped(agent_id), self.policy, self.actions, self.audit, self.today())


def create_app(llm: LLM | None = None) -> FastAPI:
    app = FastAPI(title="Chat PBG, Súper Asistente")
    app.state.s = state = State(llm)

    # La identidad llega por cabecera (en producción: sesión/JWT). Nunca del cuerpo ni del modelo.
    class ChatIn(BaseModel):
        message: str = Field(min_length=1, max_length=2000)

    @app.get("/health")
    def health():
        return {"status": "ok", "model": state.llm.name}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/agents")
    def agents():
        return [{"id": a.id, "name": a.name} for a in state.store.agents.values()]

    @app.post("/chat")
    def chat(body: ChatIn, x_agent_id: str = Header()):
        return assistant.run(state.llm, state.runner(x_agent_id), body.message, settings.max_steps)

    @app.get("/actions")
    def pending(x_agent_id: str = Header()):
        state.scoped(x_agent_id)
        return state.actions.pending_for(x_agent_id)

    @app.post("/actions/{action_id}/confirm")
    def confirm(action_id: str, x_agent_id: str = Header()):
        scoped = state.scoped(x_agent_id)
        try:
            action = state.actions.take(x_agent_id, action_id)
        except ActionError as exc:
            raise HTTPException(409, str(exc)) from exc
        action.result = execute_confirmed(scoped, action.tool, action.payload)
        action.status = "confirmada"
        state.audit.record(x_agent_id, action.tool, "ejecutada tras confirmación humana", action.payload,
                           {"action_id": action.id})
        return {"status": action.status, "result": action.result, "summary": action.summary}

    @app.post("/actions/{action_id}/reject")
    def reject(action_id: str, x_agent_id: str = Header()):
        state.scoped(x_agent_id)
        try:
            action = state.actions.take(x_agent_id, action_id)
        except ActionError as exc:
            raise HTTPException(409, str(exc)) from exc
        action.status = "rechazada"
        state.audit.record(x_agent_id, action.tool, "rechazada por el agente", action.payload,
                           {"action_id": action.id})
        return {"status": action.status}

    @app.get("/calendar")
    def calendar(x_agent_id: str = Header()):
        return state.scoped(x_agent_id).events()

    @app.get("/audit")
    def audit(x_agent_id: str = Header()):
        state.scoped(x_agent_id)
        return state.audit.for_agent(x_agent_id)

    return app


app = create_app()
