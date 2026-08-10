from __future__ import annotations

import json

from cninfo_monitor.notifications import load_workwechat_config, send_workwechat_text


def test_load_config_reads_relative_env_file_and_recipients(tmp_path, monkeypatch):
    for key in ("WORKWECHAT_CORP_ID", "WORKWECHAT_CORP_SECRET", "WORKWECHAT_AGENT_ID"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "WORKWECHAT_CORP_ID=file-corp\n"
        "WORKWECHAT_CORP_SECRET=file-secret\n"
        "WORKWECHAT_AGENT_ID=file-agent\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
notifications:
  workwechat:
    enabled: true
    env_file: ".env"
    department_ids: ["3"]
""",
        encoding="utf-8",
    )

    config = load_workwechat_config(config_path)

    assert config.corp_id == "file-corp"
    assert config.corp_secret == "file-secret"
    assert config.agent_id == "file-agent"
    assert config.department_ids == ("3",)


def test_send_workwechat_text_uses_official_api_and_splits_long_messages(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.body

    def fake_urlopen(request, timeout):
        requests.append(request)
        if "gettoken" in request.full_url:
            return FakeResponse(b'{"errcode":0,"access_token":"token"}')
        return FakeResponse(b'{"errcode":0,"errmsg":"ok"}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config_path = None
    from cninfo_monitor.notifications import WorkWechatConfig

    config = WorkWechatConfig(
        enabled=True,
        corp_id="corp",
        corp_secret="secret",
        agent_id="agent",
        user_ids=(),
        department_ids=("3",),
        tag_ids=(),
    )

    assert send_workwechat_text("一行消息\n" * 1000, config) is True
    send_requests = [request for request in requests if "message/send" in request.full_url]
    assert len(send_requests) > 1
    payload = json.loads(send_requests[0].data.decode("utf-8"))
    assert payload["toparty"] == "3"
    assert payload["msgtype"] == "text"
    assert all(
        len(
            json.loads(request.data.decode("utf-8"))["text"]["content"].encode(
                "utf-8"
            )
        )
        <= 1900
        for request in send_requests
    )


def test_send_defaults_to_all_users_when_recipient_is_empty(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.body

    def fake_urlopen(request, timeout):
        requests.append(request)
        if "gettoken" in request.full_url:
            return FakeResponse(b'{"errcode":0,"access_token":"token"}')
        return FakeResponse(b'{"errcode":0,"errmsg":"ok"}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    from cninfo_monitor.notifications import WorkWechatConfig

    config = WorkWechatConfig(
        enabled=True,
        corp_id="corp",
        corp_secret="secret",
        agent_id="agent",
        user_ids=(),
        department_ids=(),
        tag_ids=(),
    )

    assert send_workwechat_text("hello", config) is True
    payload = json.loads(requests[-1].data.decode("utf-8"))
    assert payload["touser"] == "@all"
