"""
配置管理 - 支持 JSON 配置文件 + 环境变量覆盖

配置文件: ~/.config/llm-assistant/config.json
环境变量: LLM_URL, LLM_PORT, LLM_MODEL, LLM_MAX_TOKENS, LLM_TEMPERATURE, LLM_MAX_ROUNDS
"""

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "url": "http://127.0.0.1:18080",
        "model": "local",
        "max_tokens": 2048,
        "temperature": 0.7,
        "timeout": 120,
    },
    "agent": {
        "max_rounds": 5,
    },
    "tools": {
        "command_timeout": 30,
        "read_max_chars": 5000,
        "list_max_items": 50,
        "volume_step": "5%",
        "brightness_step": "5%",
    },
    "security": {
        "audit_log": "~/.config/llm-assistant/audit.log",
        "require_confirmation": True,
        "confirm_all_commands": True,
        "confirm_writes": True,
        "confirm_reads_outside_roots": True,
        "allowed_read_roots": ["~/Documents/Codex", "~/文档/Codex", "/tmp"],
    },
    "paths": {
        "models_dir": "~/ai/models",
        "voice_script": "~/.local/bin/voice-control-run",
    },
}

ENV_MAP: dict[str, str] = {
    "LLM_URL": "llm.url",
    "LLM_PORT": "llm.url",  # special: appends port
    "LLM_MODEL": "llm.model",
    "LLM_MAX_TOKENS": "llm.max_tokens",
    "LLM_TEMPERATURE": "llm.temperature",
    "LLM_TIMEOUT": "llm.timeout",
    "LLM_MAX_ROUNDS": "agent.max_rounds",
}


def _deep_get(d: dict, key_path: str) -> Any:
    """Get nested dict value by dot-separated path."""
    keys = key_path.split(".")
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    return d


def _deep_set(d: dict, key_path: str, value: Any) -> None:
    """Set nested dict value by dot-separated path."""
    keys = key_path.split(".")
    for k in keys[:-1]:
        if k not in d:
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


def _apply_env_overrides(config: dict) -> dict:
    """Apply environment variable overrides to config."""
    for env_var, key_path in ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is None:
            continue
        if env_var == "LLM_PORT":
            # Special: append port to URL
            url = _deep_get(config, "llm.url") or "http://127.0.0.1:18080"
            base = url.rsplit(":", 1)[0] if "://" in url else url
            _deep_set(config, "llm.url", f"{base}:{value}")
            continue
        # Type conversion
        current = _deep_get(config, key_path)
        if isinstance(current, bool):
            value = value.lower() in ("1", "true", "yes")
        elif isinstance(current, int):
            try:
                value = int(value)
            except ValueError:
                continue
        elif isinstance(current, float):
            try:
                value = float(value)
            except ValueError:
                continue
        _deep_set(config, key_path, value)
    return config


class Config:
    """Configuration manager with file + env support."""

    def __init__(self, config_path: str | None = None):
        if config_path is None:
            config_path = os.path.expanduser("~/.config/llm-assistant/config.json")
        self._path = Path(config_path)
        self._data = self._load()

    def _load(self) -> dict:
        """Load config from file, merging with defaults, then apply env overrides."""
        config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy

        if self._path.exists():
            try:
                with open(self._path) as f:
                    file_data = json.load(f)
                self._deep_merge(config, file_data)
            except (json.JSONDecodeError, OSError):
                pass

        config = _apply_env_overrides(config)
        return config

    def _deep_merge(self, base: dict, override: dict) -> None:
        """Recursively merge override into base."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def save(self) -> None:
        """Save current config to file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get(self, key_path: str, default: Any = None) -> Any:
        """Get config value by dot-separated path."""
        value = _deep_get(self._data, key_path)
        return value if value is not None else default

    def set(self, key_path: str, value: Any) -> None:
        """Set config value by dot-separated path."""
        _deep_set(self._data, key_path, value)

    # Convenience properties
    @property
    def llm_url(self) -> str:
        return self.get("llm.url", "http://127.0.0.1:18080")

    @property
    def llm_model(self) -> str:
        return self.get("llm.model", "local")

    @property
    def llm_max_tokens(self) -> int:
        return self.get("llm.max_tokens", 2048)

    @property
    def llm_temperature(self) -> float:
        return self.get("llm.temperature", 0.7)

    @property
    def llm_timeout(self) -> int:
        return self.get("llm.timeout", 120)

    @property
    def agent_max_rounds(self) -> int:
        return self.get("agent.max_rounds", 5)

    @property
    def command_timeout(self) -> int:
        return self.get("tools.command_timeout", 30)

    @property
    def read_max_chars(self) -> int:
        return self.get("tools.read_max_chars", 5000)

    @property
    def audit_log_path(self) -> str:
        return os.path.expanduser(self.get("security.audit_log", "~/.config/llm-assistant/audit.log"))


# Module-level singleton
_config: Config | None = None


def get_config() -> Config:
    """Get or create the global Config singleton."""
    global _config
    if _config is None:
        _config = Config()
    return _config
