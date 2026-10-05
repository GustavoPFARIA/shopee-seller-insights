"""Outgoing e-mail over SMTP (standard library only). Disabled unless SMTP_HOST is set."""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger(__name__)
TIMEOUT_SECONDS = 20


class MailError(Exception):
    pass


def _one_line(value: str) -> str:
    """Header-safe text: no CR/LF (prevents header injection), trimmed length."""
    return " ".join(value.split())[:200]


def send_email(to: list[str], subject: str, body: str) -> None:
    """Send a plain-text e-mail; raises MailError on any delivery problem."""
    settings = get_settings()
    if not settings.email_enabled or not settings.smtp_host:
        raise MailError("E-mail is not configured (SMTP_HOST)")
    if not to:
        return
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = ", ".join(_one_line(addr) for addr in to)
    message["Subject"] = _one_line(subject)
    message.set_content(body)

    context = ssl.create_default_context()
    try:
        if settings.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=TIMEOUT_SECONDS, context=context
            )
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT_SECONDS)
        with server:
            if settings.smtp_security == "starttls":
                server.starttls(context=context)
            if settings.smtp_username and settings.smtp_password:
                server.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        # Log the failure class only: recipients are personal data.
        log.warning("e-mail delivery failed: %s", type(exc).__name__)
        raise MailError("E-mail delivery failed") from exc
