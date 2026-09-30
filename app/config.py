import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# Proveedores compatibles con la API de OpenAI: cambiar de proveedor es cambiar el .env.
PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash"),
    "openai": ("https://api.openai.com/v1", "gpt-4.1-mini"),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    llm_provider: str = "groq"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout_s: float = 30.0

    data_dir: Path = ROOT / "data"
    app_today: str | None = None  # YYYY-MM-DD fija la fecha "hoy" para demos reproducibles
    confirmation_ttl_s: int = 600
    max_steps: int = 4

    def resolved_llm(self) -> tuple[str, str, str | None]:
        base_url, model = PROVIDERS.get(self.llm_provider, PROVIDERS["groq"])
        key = self.llm_api_key or os.getenv(f"{self.llm_provider.upper()}_API_KEY")
        return self.llm_base_url or base_url, self.llm_model or model, key


settings = Settings()
