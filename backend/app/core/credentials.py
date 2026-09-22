from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger(__name__)

CREDENTIALS_PATH = "/tmp/google-credentials.json"


def materialize_google_credentials() -> None:
    """If GOOGLE_APPLICATION_CREDENTIALS_JSON is set (base64-encoded service account JSON),

    write it to a temp file and point GOOGLE_APPLICATION_CREDENTIALS at it.
    On Cloud Run / local dev without this env var, ADC handles credentials — this function is a no-op.
    """
    b64 = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not b64:
        msg = "GOOGLE_APPLICATION_CREDENTIALS_JSON not set; relying on ADC"
        print(f"[BOOT] {msg}", flush=True)
        logger.info(msg)
        return
    try:
        raw = base64.b64decode(b64)
    except Exception as exc:
        msg = f"failed to base64-decode credentials: {exc}"
        print(f"[BOOT] {msg}", flush=True)
        logger.error(msg)
        raise

    os.makedirs(os.path.dirname(CREDENTIALS_PATH), exist_ok=True)
    with open(CREDENTIALS_PATH, "wb") as f:
        f.write(raw)
    try:
        os.chmod(CREDENTIALS_PATH, 0o600)
    except Exception:
        pass
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
    msg_mat = f"materialized Google credentials at {CREDENTIALS_PATH}"
    msg_env = f"GOOGLE_APPLICATION_CREDENTIALS is now {os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')}"
    print(f"[BOOT] {msg_mat}", flush=True)
    print(f"[BOOT] {msg_env}", flush=True)
    logger.info(msg_mat)
    logger.info(msg_env)
