"""
Email through Resend's HTTP API (https://resend.com/docs/api-reference/emails/send-email).

`ResendEmailBackend` is the Django email backend, so `send_mail` and `EmailMessage` go through
Resend. Views send with `send_email`, which doesn't hold up the response: it sends on a
background thread unless `EMAIL_IN_BACKGROUND` is off (tests).
"""
import logging
import threading

import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger('main')

API_URL = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 10


class ResendError(Exception):
    """Resend refused the email or couldn't be reached"""


def _payload(message):
    payload = {
        "from": message.from_email or settings.DEFAULT_FROM_EMAIL,
        "to": message.to,
        "subject": message.subject,
        "text": message.body,
    }
    html = next((content for content, mimetype in getattr(message, "alternatives", []) if mimetype == "text/html"), None)
    if html:
        payload["html"] = html
    if message.cc:
        payload["cc"] = message.cc
    if message.bcc:
        payload["bcc"] = message.bcc
    if message.reply_to:
        payload["reply_to"] = message.reply_to
    return payload


class ResendEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            try:
                self._send(message)
                sent += 1
            except ResendError:
                if not self.fail_silently:
                    raise
        return sent

    def _send(self, message):
        if not settings.RESEND_API_KEY:
            raise ResendError("RESEND_API_KEY isn't set")
        try:
            resp = requests.post(
                API_URL,
                json=_payload(message),
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            logger.error(f"Resend couldn't be reached for '{message.subject}': {error}")
            raise ResendError(str(error)) from error
        if resp.status_code >= 400:
            logger.error(f"Resend refused '{message.subject}' ({resp.status_code}): {resp.text[:500]}")
            raise ResendError(resp.text)
        logger.info(f"Email '{message.subject}' sent to {', '.join(message.to)} (Resend id {resp.json().get('id')})")


def _deliver(message):
    try:
        message.send()
    except Exception as error:
        logger.error(f"Failed to send '{message.subject}' to {', '.join(message.to)}: {error}")


def greeting(user):
    """How an email opens: not every account has a first name"""
    name = (user.first_name or "").strip()
    return f"Hi {name}," if name else "Hi,"


def send_email(to, subject, text, html=None):
    """Send one email without waiting for it; failures are logged, never raised"""
    message = EmailMultiAlternatives(subject=subject, body=text, from_email=settings.DEFAULT_FROM_EMAIL, to=[to])
    if html:
        message.attach_alternative(html, "text/html")
    if settings.EMAIL_IN_BACKGROUND:
        threading.Thread(target=_deliver, args=(message,), daemon=True).start()
    else:
        _deliver(message)
