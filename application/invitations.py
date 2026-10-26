import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from database import DATABASE_PATH
from scheduling import access_window, reserved_window, claim_invitation, finish_invitation, validate_base_url


LOCAL_TIMEZONE = ZoneInfo("Asia/Kolkata")


def format_slot(timestamp, include_timezone=True):
    pattern = "%d %b %Y, %I:%M %p" + (" IST" if include_timezone else "")
    return datetime.fromisoformat(timestamp).astimezone(LOCAL_TIMEZONE).strftime(pattern)


def calendar_url(invitation):
    start, end = reserved_window(invitation["starts_at"], invitation["ends_at"])
    opens, closes = access_window(invitation["starts_at"], invitation["ends_at"])
    duration = int((datetime.fromisoformat(invitation["ends_at"]) - datetime.fromisoformat(invitation["starts_at"])).total_seconds() // 60)
    description = (
        f"Start your interview between {format_slot(opens.isoformat())} and {format_slot(closes.isoformat())}. "
        f"You have {duration} minutes from the moment you start. "
        "This calendar reservation covers the earliest start through the latest possible finish.\n"
        f"Open your personal invitation: {invitation['interview_url']}"
    )
    return "https://calendar.google.com/calendar/r/eventedit?" + urlencode({
        "action": "TEMPLATE", "text": f"Interview: {invitation['job_title']}",
        "dates": f"{start.strftime('%Y%m%dT%H%M%SZ')}/{end.strftime('%Y%m%dT%H%M%SZ')}",
        "ctz": "Asia/Kolkata", "details": description,
    })


def build_email(invitation, username):
    message = EmailMessage()
    message["From"] = formataddr(("Recruitment Team", username))
    message["To"] = invitation["email"]
    rescheduled = bool(invitation.get("is_rescheduled"))
    subject = "Interview rescheduled" if rescheduled else "Interview invitation"
    message["Subject"] = f"{subject} - {invitation['job_title']}"
    message["Message-ID"] = make_msgid()
    opens, closes = access_window(invitation["starts_at"], invitation["ends_at"])
    start, end = format_slot(opens.isoformat()), format_slot(closes.isoformat())
    duration = int((datetime.fromisoformat(invitation["ends_at"]) - datetime.fromisoformat(invitation["starts_at"])).total_seconds() // 60)
    calendar = calendar_url(invitation)
    introduction = (
        f"Your technical interview for the {invitation['job_title']} position has been rescheduled. Please find your updated interview details below."
        if rescheduled else
        f"We are pleased to invite you to an AI-led technical interview for the {invitation['job_title']} position."
    )
    update_note = (
        "Your previous invitation link is no longer valid. Please use the new link in this email. "
        "If you saved the previous slot to your calendar, please replace it with the updated details."
        if rescheduled else ""
    )
    message.set_content(
        f"Dear {invitation['full_name']},\n\n"
        f"{introduction}\n\n"
        + (f"{update_note}\n\n" if update_note else "") +
        f"You may start your interview from {start} until {end}.\n"
        f"Interview duration: {duration} minutes from the moment you click Start interview.\n"
        "Please start before the window closes. Once started, your full interview time remains available even after the start window closes.\n\n"
        f"Start your interview: {invitation['interview_url']}\n\n"
        f"Add to Google Calendar: {calendar}\n"
        "The calendar reservation covers the earliest start through the latest possible finish.\n\n"
        "This invitation link is personal to you. Please keep it private.\n\n"
        "We look forward to learning more about your experience and skills.\n\nKind regards,\nRecruitment Team\n"
    )
    message.add_alternative(
        '<div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;color:#173649;line-height:1.6">'
        f'<h2>{"Interview rescheduled" if rescheduled else "Technical interview invitation"}</h2>'
        f"<p>Dear {escape(invitation['full_name'])},</p>"
        f"<p>{escape(introduction)}</p>"
        + (f"<p>{escape(update_note)}</p>" if update_note else "") +
        '<div style="background:#eef4f5;padding:18px;border-radius:8px">'
        f"<strong>You may start your interview:</strong><br>From {start}<br>Until {end}<br><br>"
        f"<strong>Duration:</strong> {duration} minutes from when you click Start interview.</div>"
        "<p>Please start before the window closes. Once started, you will have your full interview time, even if the start window closes during your interview.</p>"
        f'<p><a style="display:inline-block;background:#237d78;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none" href="{escape(invitation["interview_url"], quote=True)}">Open your interview</a></p>'
        f'<p><a href="{escape(calendar, quote=True)}">Add to Google Calendar</a><br>'
        '<small>The calendar reservation covers the earliest start through the latest possible finish.</small></p>'
        "<p>This invitation link is personal to you. Please keep it private.</p>"
        "<p>We look forward to learning more about your experience and skills.</p>"
        "<p>Kind regards,<br><strong>Recruitment Team</strong></p></div>",
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
