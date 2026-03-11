"""
helpers/collaborate_email.py — Send a collaboration invite email.
"""

import asyncio
from pathlib import Path

import config

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "collaborate_email.md"


async def send_collaborate_email(
    to_email: str,
    invite_url: str,
    name: str,
    app_name: str,
    admin_name: str,
    workspace_key: str,
) -> bool:
    """Send a collaboration invite email."""
    loop = asyncio.get_event_loop()

    def _send():
        import smtplib
        from email.mime.text import MIMEText

        smtp_user = config.GMAIL_ADDRESS
        smtp_pass = config.GMAIL_APP_PASSWORD
        if not smtp_user or not smtp_pass:
            return False

        try:
            template = _TEMPLATE_PATH.read_text()
        except FileNotFoundError:
            print("[collaborate] Template not found: templates/collaborate_email.md")
            return False

        body = template.format(
            name=name or "there",
            invite_url=invite_url,
            app_name=app_name,
            admin_name=admin_name,
            workspace_key=workspace_key,
        )

        msg = MIMEText(body, "plain")
        msg["From"] = smtp_user
        msg["To"] = to_email
        msg["Subject"] = f"You're invited to collaborate on {app_name}"

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, to_email, msg.as_string())
            return True
        except Exception as e:
            print(f"[collaborate] Email send error: {e}")
            return False

    return await loop.run_in_executor(None, _send)
