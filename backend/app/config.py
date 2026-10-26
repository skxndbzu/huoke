from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./huoke.db'
    redis_url: str = 'redis://localhost:6379/0'
    operator_token: str = ''
    reviewer_token: str = ''
    real_send_enabled: bool = False
    smtp_host: str = ''
    smtp_port: int = 465
    smtp_username: str = ''
    smtp_password: str = ''
    smtp_from: str = ''
    smtp_mode: str = 'ssl'
    llm_base_url: str = ''
    llm_api_key: str = ''
    llm_model: str = ''
    llm_input_per_million: float = Field(default=0, ge=0)
    llm_output_per_million: float = Field(default=0, ge=0)
    ai_round_budget: float = Field(default=1, gt=0, le=100)
    ai_daily_budget: float = Field(default=5, gt=0, le=1000)
    ai_monthly_budget: float = Field(default=100, gt=0, le=10000)
    source_enabled: bool = True
    source_batch_size: int = Field(default=30, ge=1, le=100)
    send_interval_seconds: int = Field(default=60, ge=30)


@lru_cache
def settings():
    return Settings()
