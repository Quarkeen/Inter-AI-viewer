import sqlite3
from datetime import datetime, time, timedelta
from html import escape

import streamlit as st

from database import get_jobs
from invitations import LOCAL_TIMEZONE, send_invitation
from scheduling import access_window, check_scheduled_approval, get_approved_candidates, get_interviews, reschedule_interview, schedule_interview, utc_now
from shortlisting import get_run


st.set_page_config(layout="wide")
st.title("Interview scheduling")
st.html("""<style>
div[class*="st-key-schedule_row_"] { padding: 12px 16px; border-radius: 12px; }
.schedule-name { font-size: 1rem; font-weight: 650; overflow-wrap: anywhere; }
.schedule-meta { font-size: .8rem; opacity: .75; margin-top: 4px; overflow-wrap: anywhere; }
.schedule-label { font-size: .73rem; opacity: .7; margin-bottom: 4px; }
.schedule-slot { font-size: .88rem; line-height: 1.6; }
</style>""")
st.caption("Assign separate slots to approved applicants, then approve each invitation for sending.")
if "invitation_notice" in st.session_state:
    st.success(st.session_state.pop("invitation_notice"))

try:
    jobs = get_jobs()
    if not jobs:
        st.info("Create a job and approve applicants before scheduling interviews.")
        st.stop()
    jobs_by_id = {job["job_id"]: job for job in jobs}
    job_id = st.selectbox(
        "Select a job", list(jobs_by_id),
        format_func=lambda value: f"#{value} · {jobs_by_id[value]['title']}",
        key="schedule_job_id",
    )
    interviews = get_interviews(job_id)
    scheduled_ids = {item["application_id"] for item in interviews}
    approved_ids = set()
    run = get_run(job_id=job_id)
    if run and run["status"] == "completed":
        approved = get_approved_candidates(run["run_id"])
        approved_ids = {candidate["application_id"] for candidate in approved}
        pending = {candidate["application_id"]: candidate for candidate in approved if candidate["application_id"] not in scheduled_ids}
        if pending:
            st.subheader("Schedule a candidate")
            with st.form("schedule_candidate"):
                selected = st.selectbox(
                    "Candidate", list(pending),
                    format_func=lambda value: f"{pending[value]['full_name']} · {pending[value]['email']} · #{value}",
                )
                date_column, time_column, duration_column = st.columns(3)
                with date_column:
                    date = st.date_input("Interview date", value=datetime.now(LOCAL_TIMEZONE).date() + timedelta(days=1))
                with time_column:
                    start_time = st.time_input("Start time", value=time(10, 0))
                with duration_column:
                    duration = st.number_input("Duration (minutes)", min_value=5, max_value=60, value=30, step=5)
                st.caption("Saved slots reserve the buffered start window and full interview duration. Invitations are sent only when you click Send invitation.")
                save = st.form_submit_button("Save slot", type="primary")
            if save:
                schedule_interview(run["run_id"], selected, datetime.combine(date, start_time, LOCAL_TIMEZONE), int(duration))
                st.session_state["invitation_notice"] = "Interview slot saved. Review it below and click Send invitation when ready."
                st.rerun()
        elif approved:
            st.caption("All approved candidates have a slot. Use Reschedule below to change an individual interview.")
        else:
            st.info("No applicants were approved in the latest shortlist.")
    else:
        st.info("Complete HR approval in Shortlist review before scheduling this job's applicants.")

    if interviews:
        st.subheader("Scheduled interviews")
    labels = {
        "scheduled": "Scheduled · invitation not sent",
        "sending": "Sending attempt in progress · do not send again",
        "sent": "Invitation accepted by Gmail",
        "delivery_unknown": "Sending could not be confirmed · check the sender's Sent folder",
    }
    if interviews:
        st.caption(f"{len(interviews)} scheduled interview(s)")
        with st.container(height=480, border=False):
            for interview in interviews:
                with st.container(border=True, key=f"schedule_row_{interview['interview_id']}"):
                    reason = None
                    try:
                        check_scheduled_approval(interview["interview_id"])
                    except ValueError as error:
                        reason = str(error)
                        if interview["application_id"] in approved_ids:
                            reason = "This slot belongs to an earlier shortlist. Reschedule it to use the current approval."
                    opens, closes = access_window(interview["starts_at"], interview["ends_at"])
                    expired = closes <= utc_now()
                    identity, slot, status, manage, invitation = st.columns(
                        [2.8, 2.6, 2.1, 1.3, 1.5], gap="small", vertical_alignment="center",
                    )
                    with identity:
                        st.html(f'<div class="schedule-name">{escape(interview["full_name"])}</div><div class="schedule-meta">{escape(interview["email"])}</div>')
                        st.caption(f"#{interview['application_id']} · {interview['job_title']}")
                    with slot:
                        start_label = datetime.fromisoformat(interview["starts_at"]).astimezone(LOCAL_TIMEZONE).strftime("%d %b %Y, %I:%M %p")
                        end_label = datetime.fromisoformat(interview["ends_at"]).astimezone(LOCAL_TIMEZONE).strftime("%d %b %Y, %I:%M %p")
                        st.html(f'<div class="schedule-label">INTERVIEW SLOT</div><div class="schedule-slot"><b>From</b> {escape(start_label)}<br/><b>Until</b> {escape(end_label)}</div>')
                        minutes = int((datetime.fromisoformat(interview["ends_at"]) - datetime.fromisoformat(interview["starts_at"])).total_seconds() // 60)
                        st.caption(f"Duration: {minutes} minutes")
                    with status:
                        st.caption("INVITATION STATUS")
                        label = labels[interview["status"]]
                        if interview["status"] == "scheduled" and interview["is_rescheduled"]:
                            label = "Rescheduled · invitation not sent"
                        st.write(label)
                    with manage:
                        if interview["application_id"] in approved_ids and interview["status"] != "sending":
                            with st.popover("Reschedule", width="stretch"):
                                st.markdown("**Reschedule interview**")
                                st.caption(interview["full_name"])
                                next_slot = datetime.now(LOCAL_TIMEZONE) + timedelta(minutes=10)
                                with st.form(f"reschedule_{interview['interview_id']}"):
                                    new_date = st.date_input("New date", value=next_slot.date(), key=f"new_date_{interview['interview_id']}")
                                    new_time = st.time_input("New start time", value=next_slot.time().replace(second=0, microsecond=0), key=f"new_time_{interview['interview_id']}")
                                    new_duration = st.number_input("New duration (minutes)", min_value=5, max_value=60, value=30, step=5, key=f"new_duration_{interview['interview_id']}")
                                    st.caption("Saving invalidates the old invitation. A new link is created only when you click Send invitation again.")
                                    reschedule = st.form_submit_button("Save new slot")
                                if reschedule:
                                    reschedule_interview(interview["interview_id"], datetime.combine(new_date, new_time, LOCAL_TIMEZONE), int(new_duration))
                                    st.session_state["invitation_notice"] = "New slot saved. Click Send invitation to send a fresh link."
                                    st.rerun()
                    with invitation:
                        send = st.button(
                            "Send invitation", key=f"send_invitation_{interview['interview_id']}",
                            type="primary", icon=":material/mail:", width="stretch",
                            disabled=interview["status"] != "scheduled" or expired or reason is not None,
                        )
                    if reason is not None:
                        st.warning(reason)
                    if expired and interview["status"] == "scheduled":
                        st.warning("This slot has expired. Save a future schedule before sending.")
                    if send:
                        with st.spinner("Sending invitation…"):
                            send_invitation(interview["interview_id"], st.secrets)
                        st.session_state["invitation_notice"] = (
                            f"Gmail accepted the invitation for {interview['full_name']}. Check the recipient inbox to confirm delivery."
                        )
                        st.rerun()
except (ValueError, RuntimeError) as error:
    st.error(str(error))
except FileNotFoundError:
    st.error("Configure the sender in the local secrets file before sending invitations.")
except sqlite3.Error:
    st.error("The schedule could not be loaded or saved. Please try again.")
