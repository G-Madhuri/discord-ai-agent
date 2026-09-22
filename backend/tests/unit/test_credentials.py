from __future__ import annotations

import base64
import os
import pytest
from app.core import credentials


def test_materialize_writes_file_and_sets_env(monkeypatch, tmp_path):
    orig_env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    try:
        monkeypatch.setattr(
            credentials, "CREDENTIALS_PATH", str(tmp_path / "creds.json")
        )
        payload = b'{"type":"service_account"}'
        monkeypatch.setenv(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON",
            base64.b64encode(payload).decode(),
        )
        credentials.materialize_google_credentials()
        assert os.path.exists(credentials.CREDENTIALS_PATH)
        with open(credentials.CREDENTIALS_PATH, "rb") as f:
            assert f.read() == payload
        assert (
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
            == credentials.CREDENTIALS_PATH
        )
    finally:
        if orig_env is not None:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = orig_env
        else:
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)


def test_materialize_noop_when_env_var_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(
        credentials, "CREDENTIALS_PATH", str(tmp_path / "creds.json")
    )

    credentials.materialize_google_credentials()
    assert not os.path.exists(credentials.CREDENTIALS_PATH)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ
