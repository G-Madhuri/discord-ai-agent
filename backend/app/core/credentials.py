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
        logger.info("GOOGLE_APPLICATION_CREDENTIALS_JSON not set; relying on ADC")
        return
    try:
        raw = base64.b64decode(b64)
    except Exception as exc:
        logger.error("failed to base64-decode credentials: %s", exc)
        raise

    os.makedirs(os.path.dirname(CREDENTIALS_PATH), exist_ok=True)
    with open(CREDENTIALS_PATH, "wb") as f:
        f.write(raw)
    try:
        os.chmod(CREDENTIALS_PATH, 0o600)
    except Exception:
        pass
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
    logger.info("materialized Google credentials at %s", CREDENTIALS_PATH)
    logger.info(
        "GOOGLE_APPLICATION_CREDENTIALS is now %s",
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
    )
