from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """后端配置，密钥只从环境变量或 .env 读取，不写入代码。"""

    mimo_api_key: str = ""
    mimo_base_url: str = "https://token-plan-cn.xiaomimimo.com/v1"
    mimo_model: str = "mimo-v2.5"
    llm_enabled: bool = True
    agent_memory_limit: int = 30
    max_autonomy_steps: int = 10000
    loop_cooldown_steps: int = 3
    llm_decision_chance: float = 0.35
    agent_profiles_dir: str = "prompts/agents"
    agent_memory_dir: str = "memory"
    memory_recent_events: int = 12
    memory_keep_tail_events: int = 80
    llm_context_window_tokens: int = 1048576
    memory_compact_context_ratio: float = 0.8
    break_chance: float = 0.06
    low_energy_rest_threshold: float = 0.32
    high_stress_rest_threshold: float = 0.78

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
