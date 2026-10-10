"""Outbound "SMS" for local use: every message is delivered to Mailpit (https://mailpit.axllent.org).

Mailpit is a local mail catcher. Each SMS becomes an email addressed to ``<phone>@sms.janamdatri.local``
and appears in the Mailpit inbox (http://localhost:8025) – OTPs, ASHA alerts and family alerts alike.
Nothing leaves the computer. With SMS_CHANNEL=log messages are only written to the server log.
"""
from __future__ import annotations

import logging
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.core.config import settings

log = logging.getLogger("janamdatri.sms")
outbox: list[dict] = []  # last messages (also kept for tests)


class SMSDeliveryError(RuntimeError):
    pass


def sms_address(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return f"{digits}@{settings.sms_email_domain}"


def _to_mailpit(phone: str, text: str) -> None:
    msg = EmailMessage()
    msg["From"] = formataddr(("Janamdatri-AI SMS", f"sms@{settings.sms_email_domain}"))
    msg["To"] = formataddr((phone, sms_address(phone)))
    first = text.split(". ")[0]
    msg["Subject"] = f"SMS to {phone}: {first[:80]}"
    msg["Message-ID"] = make_msgid(domain=settings.sms_email_domain)
    msg["X-Janamdatri-Phone"] = phone
    msg.set_content(text)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as smtp:
        smtp.send_message(msg)


def send_sms(phone: str, text: str, *, critical: bool = False) -> bool:
    """Deliver a text message. With ``critical`` (OTPs) a delivery failure raises SMSDeliveryError."""
    outbox.append({"to": phone, "text": text})
    del outbox[:-200]
    log.info("SMS to •••%s: %s", phone[-4:], text)
    if settings.sms_channel != "mailpit":
        return True
    try:
        _to_mailpit(phone, text)
        return True
    except OSError as exc:  # connection refused, timeout, SMTP errors
        log.warning("Could not deliver SMS to Mailpit at %s:%s (%s)", settings.smtp_host, settings.smtp_port, exc)
        if critical:
            raise SMSDeliveryError(str(exc)) from exc
        return False
