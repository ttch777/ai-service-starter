"""Runtime settings loaded from environment / .env.

路径全部基于项目根解析，所以无论从哪个工作目录启动（PyCharm 默认、VS Code、
uvicorn 命令行、Docker），都能找到 config 和 .env。不要改回相对路径——
这是本地开发最常见的「明明文件在那儿却说找不到」的根因。
"""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

# 关键：pydantic-settings 只把 .env 读进 Settings 对象，不会注入 os.environ。
# 而 registry 需要按「环境变量名」动态取 key（api_key_env 是配置项），
# 所以必须显式加载一次，否则 .env 里的 key 永远读不到。
# override=False：真实环境变量优先于 .env（Docker / CI 场景需要这个行为）。
load_dotenv(BASE_DIR / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    config_path: Path = BASE_DIR / "config" / "models.yaml"
    log_level: str = "INFO"

    request_timeout: float = 60.0
    max_retries: int = 2
    retry_base_delay: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
