"""
helpers/invite_email.py — send an invite email to a new user.

Extracted from bot.py `_send_invite_email()` and `_INVITE_TEMPLATE_PATH`.
"""

import asyncio
from pathlib import Path

import config

_INVITE_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "invite_email.md"


async def send_invite_email(to_email: str, invite_url: str, name: str = "") -> bool:
    """Send an invite email with instructions on joining the bot."""
    loop = asyncio.get_event_loop()

    def _send():
        import smtplib
        from email.mime.text import MIMEText

        smtp_user = config.GMAIL_ADDRESS
        smtp_pass = config.GMAIL_APP_PASSWORD
        if not smtp_user or not smtp_pass:
            return False

        try:
            template = _INVITE_TEMPLATE_PATH.read_text()
        except FileNotFoundError:
            print("[invite] Template not found: templates/invite_email.md")
            return False

        body = template.format(name=name or "there", invite_url=invite_url)

        msg = MIMEText(body, "plain")
        msg["From"] = smtp_user
        msg["To"] = to_email
        msg["Subject"] = "You're invited to build apps with AI"

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, to_email, msg.as_string())
            return True
        except Exception as e:
            print(f"[invite] Email send error: {e}")
            return False

    return await loop.run_in_executor(None, _send)
