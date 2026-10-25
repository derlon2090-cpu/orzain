"""Verification and recovery email delivery with safe local development logging."""
from email.message import EmailMessage
import json
import logging
import os
import smtplib
import ssl


logger = logging.getLogger("tradegen.email")


class EmailDeliveryConfigurationError(Exception):
    pass


def assert_email_delivery_configured() -> None:
    if not os.getenv("SMTP_HOST") or not os.getenv("SMTP_FROM"):
        raise EmailDeliveryConfigurationError("SMTP_HOST and SMTP_FROM must be configured")
    if not os.getenv("PUBLIC_ORIGIN", "").startswith("https://"):
        raise EmailDeliveryConfigurationError("PUBLIC_ORIGIN must use HTTPS for production email")
    if bool(os.getenv("SMTP_USERNAME")) != bool(os.getenv("SMTP_PASSWORD")):
        raise EmailDeliveryConfigurationError("SMTP_USERNAME and SMTP_PASSWORD must be set together")


class EmailService:
    """Logs one-time tokens only in development; sends them through TLS SMTP in production."""

    def __init__(self, *, production: bool | None = None):
        self.production = (os.getenv("APP_ENV", "development").lower() == "production") if production is None else production

    def send_verification(self, recipient: str, token: str) -> None:
        self._deliver(recipient, token, "verify-email", "Verify your TRADEGEN AI account", "24 hours")

    def send_password_reset(self, recipient: str, token: str) -> None:
        self._deliver(recipient, token, "password-reset/complete", "Reset your TRADEGEN AI password", "30 minutes")

    def _deliver(self, recipient: str, token: str, endpoint: str, subject: str, expiry: str) -> None:
        if not self.production:
            logger.warning("Development one-time email token recipient=%s purpose=%s token=%s", recipient, endpoint, token)
            return
        assert_email_delivery_configured()
        host, port = os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587"))
        origin = os.environ["PUBLIC_ORIGIN"].rstrip("/")
        message = EmailMessage()
        message["Subject"], message["From"], message["To"] = subject, os.environ["SMTP_FROM"], recipient
        message.set_content(
            f"Use this one-time token at {origin}/api/v1/auth/{endpoint}:\n\n"
            f"{json.dumps({'token': token})}\n\nIt expires in {expiry}. If you did not request this, ignore this email."
        )
        timeout = float(os.getenv("SMTP_TIMEOUT_SECONDS", "10"))
        context = ssl.create_default_context()
        username, password = os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD")
        if os.getenv("SMTP_USE_SSL", "false").lower() == "true":
            with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as smtp:
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(message)


def send_verification_email(recipient: str, token: str) -> None:
    EmailService(production=True).send_verification(recipient, token)
