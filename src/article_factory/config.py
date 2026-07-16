from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    control_plane_url: str = "http://127.0.0.1:8000"
    cms_url: str = "http://127.0.0.1:8200"
    cms_api_key: str = ""
    factory_api_key: str = ""
    database_url: str = "sqlite:///./data/factory.db"
    default_puller: str = ""
    default_model: str = ""
    gateway_id: str = ""
    gateway_display_name: str = "Article Factory"
    heartbeat_interval_seconds: int = 15
    dispatch_interval_seconds: float = 1.0
    step_poll_interval_seconds: float = 1.5
    step_no_puller_timeout_seconds: float = 180.0
    step_no_puller_max_attempts: int = 3
    step_empty_response_max_attempts: int = 3
    step_response_timeout_seconds: float = 900.0
    step_busy_puller_max_wait_seconds: float = 3600.0
    step_puller_alive_check_interval_seconds: float = 10.0
    step_puller_stale_grace_seconds: float = 120.0
    step_task_status_check_interval_seconds: float = 5.0
    host: str = "0.0.0.0"
    port: int = 8100
    max_review_rounds: int = 5
    flows_root: str = "./data/flows"
    flow_run_outputs_root: str = "./data/runs"
    brave_search_api_key: str = ""
    cors_origins: str = "*"
    trust_proxy_headers: bool = False
    telemetry_csv_iteration_columns: int = 11

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


settings = Settings()
