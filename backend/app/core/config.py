from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "ResearchPilot-Agent V5"
    app_version: str = "5.0.0"
    debug: bool = True
    api_prefix: str = "/api"

    database_url: str = f"sqlite:///{ROOT / 'storage' / 'researchpilot.db'}"
    storage_dir: str = str(ROOT / "storage")

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.2

    semantic_scholar_api_key: str = ""
    max_upload_mb: int = 50
    max_research_papers: int = 12
    citation_retry_limit: int = 2
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080"

    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


settings = Settings()
