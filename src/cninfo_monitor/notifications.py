from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


WORKWECHAT_CHUNK_SIZE = 1900


@dataclass(frozen=True)
class WorkWechatConfig:
    enabled: bool = False
    corp_id: str = ""
    corp_secret: str = ""
    agent_id: str = ""
    user_ids: tuple[str, ...] = ()
    department_ids: tuple[str, ...] = ()
    tag_ids: tuple[str, ...] = ()


def load_workwechat_config(path: str | Path) -> WorkWechatConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}
    workwechat = raw.get("notifications", {}).get("workwechat", {})
    env_file = Path(str(workwechat.get("env_file", ".env")))
    if not env_file.is_absolute():
        env_file = config_path.parent / env_file
    env_values = _load_env_file(env_file)

    def credential(config_key: str, default_env_key: str) -> str:
        env_key = str(workwechat.get(f"{config_key}_env", default_env_key))
        return os.environ.get(
            env_key,
            str(workwechat.get(config_key, "") or env_values.get(env_key, "")),
        )

    return WorkWechatConfig(
        enabled=bool(workwechat.get("enabled", False)),
        corp_id=credential("corp_id", "WORKWECHAT_CORP_ID"),
        corp_secret=credential("corp_secret", "WORKWECHAT_CORP_SECRET"),
        agent_id=credential("agent_id", "WORKWECHAT_AGENT_ID"),
        user_ids=_load_string_tuple(workwechat.get("user_ids", [])),
        department_ids=_load_string_tuple(
            workwechat.get("department_ids", workwechat.get("department_id", []))
        ),
        tag_ids=_load_string_tuple(workwechat.get("tag_ids", [])),
    )


def send_workwechat_text(message: str, config: WorkWechatConfig) -> bool:
    if not config.enabled:
        return False
    if not config.corp_id or not config.corp_secret or not config.agent_id:
        return False
    try:
        access_token = _get_access_token(config)
        for chunk in _split_text(message, WORKWECHAT_CHUNK_SIZE):
            result = _send_text_chunk(access_token, chunk, config)
            if result.get("errcode", 0) != 0:
                return False
    except Exception:
        return False
    return True


def _get_access_token(config: WorkWechatConfig) -> str:
    query = urllib.parse.urlencode(
        {"corpid": config.corp_id, "corpsecret": config.corp_secret}
    )
    request = urllib.request.Request(
        f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?{query}",
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("errcode", 0) != 0 or not result.get("access_token"):
        raise RuntimeError(f"Work WeChat gettoken failed: {result}")
    return str(result["access_token"])


def _send_text_chunk(
    access_token: str,
    message: str,
    config: WorkWechatConfig,
) -> dict[str, Any]:
    url = (
        "https://qyapi.weixin.qq.com/cgi-bin/message/send?"
        + urllib.parse.urlencode({"access_token": access_token})
    )
    payload: dict[str, Any] = {
        "msgtype": "text",
        "agentid": config.agent_id,
        "text": {"content": message},
        "safe": 0,
    }
    if config.user_ids:
        payload["touser"] = "|".join(config.user_ids)
    if config.department_ids:
        payload["toparty"] = "|".join(config.department_ids)
    if config.tag_ids:
        payload["totag"] = "|".join(config.tag_ids)
    if not any(key in payload for key in ("touser", "toparty", "totag")):
        payload["touser"] = "@all"

    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    with path.open(encoding="utf-8") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def _load_string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(part.strip() for part in str(value).split("|") if part.strip())


def _split_text(text: str, chunk_size: int) -> list[str]:
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining.encode("utf-8")) <= chunk_size:
            chunks.append(remaining)
            break
        prefix_length = _utf8_prefix_length(remaining, chunk_size)
        split_at = remaining.rfind("\n", 0, prefix_length + 1)
        if split_at <= 0:
            split_at = prefix_length
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")
    return chunks or [""]


def _utf8_prefix_length(text: str, max_bytes: int) -> int:
    byte_count = 0
    for index, character in enumerate(text):
        byte_count += len(character.encode("utf-8"))
        if byte_count > max_bytes:
            return index
    return len(text)
