from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


DEFAULT_RESEND_FROM_EMAIL = "onboarding@resend.dev"


def send_resend_email(
    *,
    to: str | list[str],
    subject: str,
    html: str,
    from_email: str | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Send an HTML email through Resend using RESEND_API_KEY from the environment."""
    if project_root is not None:
        load_dotenv(project_root / ".env")
    else:
        load_dotenv()

    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("缺少 RESEND_API_KEY，请在 .env 中配置 Resend API Key。")

    sender = (from_email or os.getenv("RESEND_FROM_EMAIL", "").strip() or DEFAULT_RESEND_FROM_EMAIL).strip()
    recipients = [to] if isinstance(to, str) else [item.strip() for item in to if item.strip()]
    if not recipients:
        raise ValueError("邮件收件人不能为空。")
    if not subject.strip():
        raise ValueError("邮件 subject 不能为空。")
    if not html.strip():
        raise ValueError("邮件 html 内容不能为空。")

    try:
        import resend
    except ImportError as exc:
        raise RuntimeError("缺少 resend 依赖，请先执行 pip install -r requirements.txt。") from exc

    resend.api_key = api_key
    response = resend.Emails.send(
        {
            "from": sender,
            "to": recipients,
            "subject": subject,
            "html": html,
        }
    )
    return dict(response) if isinstance(response, dict) else {"response": response}

