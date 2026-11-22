"""
Sends the report by email through Gmail's SMTP server.

Gmail needs an *App Password* (not your normal password):
  Google Account -> Security -> 2-Step Verification (must be on) -> App passwords.
Put it in .env as EMAIL_APP_PASSWORD.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from stock_agent import config

log = logging.getLogger(__name__)


def email_configured() -> bool:
    return bool(config.EMAIL_SENDER and config.EMAIL_APP_PASSWORD and config.EMAIL_RECIPIENT)


def send_report(subject: str, html_body: str, attachments: list[Path]) -> bool:
    if not email_configured():
        log.warning("Email not configured (EMAIL_SENDER / EMAIL_APP_PASSWORD in .env) - skipping")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_SENDER
    msg["To"] = config.EMAIL_RECIPIENT
    msg.set_content("Your email app does not show HTML. Open the attached report instead.")
    msg.add_alternative(html_body, subtype="html")
    for path in attachments:
        subtype = "html" if path.suffix == ".html" else "csv"
        msg.add_attachment(path.read_bytes(), maintype="text", subtype=subtype, filename=path.name)

    try:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT,
                              context=ssl.create_default_context()) as server:
            server.login(config.EMAIL_SENDER, config.EMAIL_APP_PASSWORD)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        log.error("Gmail rejected the login. Use a 16-character App Password, not your account password.")
        return False
    except Exception as exc:
        log.error("Email failed: %s", exc)
        return False
    log.info("📧 Report emailed to %s", config.EMAIL_RECIPIENT)
    return True
