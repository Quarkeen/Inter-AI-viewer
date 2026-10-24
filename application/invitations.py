import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from database import DATABASE_PATH
from scheduling import BUFFER, claim_invitation, finish_invitation, validate_base_url


LOCAL_TIMEZONE = ZoneInfo("Asia/Kolkata")


def format_slot(timestamp):
    return datetime.fromisoformat(timestamp).astimezone(LOCAL_TIMEZONE).strftime("%d %b %Y, %I:%M %p IST")


def calendar_url(invitation):
    start = datetime.fromisoformat(invitation["starts_at"]) - BUFFER
    end = datetime.fromisoformat(invitation["ends_at"]) + BUFFER
    description = (
        f"Interview access: {format_slot(invitation['starts_at'])} to {format_slot(invitation['ends_at'])}. "
        "This calendar reservation includes 5 minutes before and after the interview.\n"
        f"Open your personal invitation: {invitation['interview_url']}"
    )
    return "https://calendar.google.com/calendar/r/eventedit?" + urlencode({
        "action": "TEMPLATE", "text": f"Interview: {invitation['job_title']}",
        "dates": f"{start.strftime('%Y%m%dT%H%M%SZ')}/{end.strftime('%Y%m%dT%H%M%SZ')}",
        "ctz": "Asia/Kolkata", "details": description,
    })


def build_email(invitation, username):
    message = EmailMessage()
    message["From"] = formataddr(("Capstone Interviews", username))
    message["To"] = invitation["email"]
    message["Subject"] = f"Interview invitation - {invitation['job_title']}"
    message["Message-ID"] = make_msgid()
    start, end = format_slot(invitation["starts_at"]), format_slot(invitation["ends_at"])
    calendar = calendar_url(invitation)
    message.set_content(
        f"Hello {invitation['full_name']},\n\nYou are invited to interview for {invitation['job_title']}.\n"
        f"Start: {start}\nEnd: {end}\n\nInterview link: {invitation['interview_url']}\n\n"
        f"Add to Google Calendar (includes 5-minute buffers): {calendar}\n\n"
        "The link allows interview access only during your assigned slot. Please do not share it.\n"
    )
    message.add_alternative(
        f"<p>Hello {escape(invitation['full_name'])},</p>"
        f"<p>You are invited to interview for <strong>{escape(invitation['job_title'])}</strong>.</p>"
        f"<p><strong>Start:</strong> {start}<br><strong>End:</strong> {end}</p>"
        f'<p><a href="{escape(invitation["interview_url"],quote=True)}">Open interview invitation</a></p>'
        f'<p><a href="{escape(calendar,quote=True)}">Add to Google Calendar</a></p>'
        "<p>The calendar reservation includes 5 minutes before and after the slot. "
        "Interview access is limited to the start and end times above. Please do not share your link.</p>",
        subtype="html",
    )
    return message


def send_invitation(interview_id, settings, database_path=DATABASE_PATH):
    username = str(settings.get("SMTP_USERNAME", "")).strip()
    password = str(settings.get("SMTP_PASSWORD", "")).replace(" ", "")
    if not username or not password:
        raise ValueError("Add SMTP_USERNAME and SMTP_PASSWORD to the local secrets file before sending.")
    base_url = validate_base_url(str(settings.get("APP_BASE_URL", "http://localhost:8501")))
    # Authenticate first: missing/incorrect credentials must not generate a link.
    try:
        client = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=25)
        try:
            client.login(username, password)
        except Exception:
            client.close()
            raise
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError("Gmail rejected the sender credentials. Use a Google app password, not your account password.") from None
    except (smtplib.SMTPException, OSError):
        raise RuntimeError("Could not connect or sign in to Gmail. Check the sender settings and connection.") from None
    try:
        invitation = claim_invitation(interview_id, base_url, database_path)
        try:
            message = build_email(invitation, username)
        except (ValueError, TypeError):
            finish_invitation(interview_id, "scheduled", database_path=database_path)
            raise ValueError("The email could not be prepared. Check the sender email and job title.") from None
        try:
            refused = client.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
            finish_invitation(interview_id, "scheduled", database_path=database_path)
            raise RuntimeError("Gmail did not accept this invitation. No active link was kept; check the recipient and retry.") from None
        except (smtplib.SMTPException, OSError):
            finish_invitation(interview_id, "delivery_unknown", str(message["Message-ID"]), database_path)
            raise RuntimeError("Gmail's acceptance could not be confirmed. Check the sender's Sent folder before taking further action.") from None
        finish_invitation(interview_id, "sent", str(message["Message-ID"]), database_path)
    finally:
        client.close()
