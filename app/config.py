from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ORBIT Backend"
    api_prefix: str = "/api"
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "https://orbit-frontend-murex.vercel.app",
    ]
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()