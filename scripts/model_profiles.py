"""读取模型配置，并为 DeepSeek Harness 准备进程环境。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import getproxies
from typing import Any

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config/model_profiles.json"
CREDENTIAL_ENV = "RESEARCHFACTORY_MODEL_API_KEY"
SUPPORTED_APIS = {"openai-completions", "openai-responses", "anthropic-messages"}
TOOL_CREDENTIALS = ("SERPER_KEY", "JINA_KEY", "MINERU_TOKEN")


def load_tool_env() -> dict[str, str]:
    """让不同模型共用 .env 中的资料服务凭证，进程环境可覆盖同名值。"""
    values = dotenv_values(PROJECT_ROOT / ".env", interpolate=False)
    return {
        key: str(os.environ.get(key, values.get(key) or "")).strip()
        for key in TOOL_CREDENTIALS
        if os.environ.get(key, values.get(key) or "")
    }


def load_system_proxy_env() -> dict[str, str]:
    """将系统 HTTP 代理传给 Node，保持与 Python requests 的联网方式一致。"""
    proxies = getproxies()
    return {f"{key.upper()}_PROXY": value for key, value in proxies.items()
            if key in {"http", "https", "all", "no"} and value}


def load_runtime_env() -> dict[str, str]:
    """读取本机运行框架配置，让 Node 路径等设置与模型选择分开保存。"""
    path = PROJECT_ROOT / ".env.runtime"
    if not path.is_file():
        return {}
    values = dotenv_values(path, interpolate=False)
    if any(value is None or not value.strip() for value in values.values()):
        raise ValueError(f"运行框架配置包含空值：{path}")
    return dict(values)


@dataclass(frozen=True)
class ModelProfile:
    """一套模型连接配置；密钥不参与对象的文本展示。"""

    name: str
    provider: str
    api: str
    input_modalities: tuple[str, ...]
    base_url: str
    model: str
    env_file: Path
    api_key: str = field(repr=False)

    def public_config(self) -> dict[str, Any]:
        """返回可供日志和预检展示的非敏感字段。"""
        return {
            "profile": self.name,
            "provider": self.provider,
            "api": self.api,
            "input": list(self.input_modalities),
            "base_url": self.base_url,
            "model": self.model,
            "credentials_file": str(self.env_file),
        }

    def runtime_env(self) -> dict[str, str]:
        """覆盖模型相关变量，使一次运行只使用选中配置的凭证。"""
        return {
            **load_system_proxy_env(),
            **os.environ,
            **load_runtime_env(),
            **load_tool_env(),
            "RESEARCHFACTORY_PYTHON": str(PROJECT_ROOT / ".venv/bin/python"),
            CREDENTIAL_ENV: self.api_key,
            "API_BASE": self.base_url,
            "API_KEY": self.api_key,
            "MODEL_NAME": self.model,
            "DEEPSEEK_PROVIDER": self.provider,
            "DEEPSEEK_BASE_URL": self.base_url,
            "DEEPSEEK_API_PROTOCOL": self.api,
            "DEEPSEEK_API_KEY_ENV": CREDENTIAL_ENV,
            "DEEPSEEK_MODEL": self.model,
            "DEEPSEEK_MODEL_INPUT": json.dumps(self.input_modalities),
            "DEEPSEEK_HARNESS_PERMISSION_MODE": "workspace-write",
            "DEEPSEEK_HARNESS_APPROVAL_POLICY": "never",
        }


def load_profile(
    name: str | None = None,
    *,
    model: str | None = None,
    config_path: Path = DEFAULT_CONFIG,
) -> ModelProfile:
    """从选中的 dotenv 文件读取端点和密钥，不合并其他配置的值。"""
    with config_path.open(encoding="utf-8") as stream:
        config = json.load(stream)
    selected = name or config["default_profile"]
    profiles = config["profiles"]
    if selected not in profiles:
        raise ValueError(f"未知模型配置 {selected!r}；可选：{', '.join(profiles)}")
    entry = profiles[selected]
    api = entry["api"]
    modalities = tuple(entry["input"])
    if not modalities or any(value not in {"text", "image"} for value in modalities):
        raise ValueError(f"模型配置 {selected!r} 的 input 只能声明 text 或 image")
    if api not in SUPPORTED_APIS:
        raise ValueError(f"模型配置 {selected!r} 的 api 不受支持：{api!r}")
    env_file = (PROJECT_ROOT / entry["env_file"]).resolve()
    if not env_file.is_file():
        raise FileNotFoundError(f"模型凭证文件不存在：{env_file}")
    # 禁用变量展开，保留密钥中可能出现的 $ 字符。
    values = dotenv_values(env_file, interpolate=False)
    required = ["API_BASE", "API_KEY"] + ([] if model is not None else ["MODEL_NAME"])
    missing = [key for key in required if not (values.get(key) or "").strip()]
    if missing:
        raise ValueError(f"{env_file} 缺少配置：{', '.join(missing)}")
    base_url = values["API_BASE"].strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{env_file} 的 API_BASE 必须是 http 或 https 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{env_file} 的 API_BASE 只填写端点地址，认证信息填写在 API_KEY")
    selected_model = (model if model is not None else values["MODEL_NAME"]).strip()
    if not selected_model:
        raise ValueError("模型名不能为空")
    return ModelProfile(
        name=selected,
        provider=entry["provider"],
        api=api,
        input_modalities=modalities,
        base_url=base_url,
        model=selected_model,
        env_file=env_file,
        api_key=values["API_KEY"].strip(),
    )
