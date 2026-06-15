from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """后端配置，密钥只从环境变量或 .env 读取，不写入代码。"""

    mimo_api_key: str = ""
    mimo_base_url: str = "https://token-plan-cn.xiaomimimo.com/v1"
    mimo_model: str = "mimo-v2.5"
    llm_enabled: bool = True
    agent_memory_limit: int = 30
    agent_profiles_dir: str = "prompts/agents"
    agent_memory_dir: str = "memory"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
