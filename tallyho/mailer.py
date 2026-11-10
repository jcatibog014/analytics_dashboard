"""Sending email (optional).

On the live site it can go through Gmail with an app password (MAIL_USERNAME
and MAIL_PASSWORD on Render). Without them, and always in tests, the email is
only kept in a list and written to the log: Tally Ho! works fine without
email, because every number is on the site too.
"""
import smtplib
from email.message import EmailMessage

from flask import current_app

GMAIL_HOST = "smtp.gmail.com"


def enabled(app=None):
    app = app or current_app
    return bool(app.config.get("MAIL_USERNAME") and app.config.get("MAIL_PASSWORD"))


def outbox(app=None):
    """Emails that weren't really sent (tests read this)."""
    return (app or current_app).extensions.setdefault("tallyho_outbox", [])


def send(to, subject, body):
    app = current_app
    message = EmailMessage()
    sender = app.config.get("MAIL_USERNAME") or "tally-ho@localhost"
    message["From"] = f"Tally Ho! <{sender}>"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    if app.testing or not enabled(app):
        outbox(app).append(message)
        app.logger.info("Email not sent (MAIL_USERNAME isn't set). To %s: %s", to, subject)
        return
    with smtplib.SMTP_SSL(GMAIL_HOST, 465, timeout=20) as smtp:
        smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
        smtp.send_message(message)
