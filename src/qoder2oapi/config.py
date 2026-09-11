from pathlib import Path
import secrets
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    qoder2oapi_api_key: str = ""
    qoder2oapi_host: str = "127.0.0.1"
    qoder2oapi_port: int = 8000
    qoder2oapi_data_dir: str = "./data"


settings = Settings()

data_dir_path = Path(settings.qoder2oapi_data_dir).resolve()
data_dir_path.mkdir(parents=True, exist_ok=True)

api_key_file = data_dir_path / "api_key.txt"
if not settings.qoder2oapi_api_key:
    if api_key_file.exists():
        settings.qoder2oapi_api_key = api_key_file.read_text(encoding="utf-8").strip()
    else:
        generated_key = secrets.token_urlsafe(32)
        api_key_file.write_text(generated_key, encoding="utf-8")
        settings.qoder2oapi_api_key = generated_key
        print(f"[Qoder2OAPI] Generated new proxy API key: {generated_key}")
