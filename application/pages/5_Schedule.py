import sqlite3
from datetime import datetime, time, timedelta

import streamlit as st

from database import get_jobs
from invitations import LOCAL_TIMEZONE, format_slot, send_invitation
from scheduling import check_scheduled_approval, get_approved_candidates, get_interviews, reschedule_interview, schedule_interviews, utc_now
from shortlisting import get_run


st.title("Interview scheduling")
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
    run = get_run(job_id=job_id)
    if run and run["status"] == "completed":
        approved = get_approved_candidates(run["run_id"])
        if approved:
            st.subheader("Assign interview slots")
            st.caption(f"{len(approved)} approved applicant(s). All times shown in India Standard Time (IST).")
            with st.expander("Applicants in scheduling order"):
                for number, candidate in enumerate(approved, 1):
                    st.write(f"{number}. {candidate['full_name']} · Application #{candidate['application_id']}")
            with st.form("schedule_interviews"):
                date_column, time_column, duration_column = st.columns(3)
                with date_column:
                    date = st.date_input("First interview date", value=datetime.now(LOCAL_TIMEZONE).date() + timedelta(days=1))
                with time_column:
                    start_time = st.time_input("First interview starts", value=time(10, 0))
                with duration_column:
                    duration = st.number_input("Duration (minutes)", min_value=5, max_value=60, value=20, step=5)
                st.caption("Each slot reserves 5 minutes before and after. Saving replaces unsent slots in this review; invitations already being sent or sent are kept.")
                save = st.form_submit_button("Save schedule", type="primary")
            if save:
                count = schedule_interviews(run["run_id"], datetime.combine(date, start_time, LOCAL_TIMEZONE), int(duration))
                st.success(f"Saved {count} interview slot(s). No links have been generated and no emails sent.")
        else:
            st.info("No applicants were approved in the latest shortlist.")
    else:
        st.info("Complete HR approval in Shortlist review before scheduling this job's applicants.")

    interviews = get_interviews(job_id)
    if interviews:
        st.subheader("Scheduled interviews")
    labels = {
        "scheduled": "Scheduled · invitation not sent",
        "sending": "Sending attempt in progress · do not send again",
        "sent": "Invitation accepted by Gmail",
        "delivery_unknown": "Sending could not be confirmed · check the sender's Sent folder",
    }
    for interview in interviews:
        with st.container(border=True):
            st.subheader(interview["full_name"])
            st.text(interview["email"])
            st.caption(f"Application #{interview['application_id']} · {interview['job_title']}")
            st.write(f"**Start:** {format_slot(interview['starts_at'])}")
            st.write(f"**End:** {format_slot(interview['ends_at'])}")
            st.caption(labels[interview["status"]])
            reason = None
            try:
                check_scheduled_approval(interview["interview_id"])
            except ValueError as error:
                reason = str(error)
                st.warning(reason)
            expired = datetime.fromisoformat(interview["ends_at"]) <= utc_now()
            if expired and interview["status"] == "scheduled":
                st.warning("This slot has expired. Save a future schedule before sending.")
            if reason is None and interview["status"] != "sending":
                with st.expander("Reschedule this interview"):
                    next_slot = datetime.now(LOCAL_TIMEZONE) + timedelta(minutes=10)
                    with st.form(f"reschedule_{interview['interview_id']}"):
                        new_date = st.date_input("New date", value=next_slot.date(), key=f"new_date_{interview['interview_id']}")
                        new_time = st.time_input("New start time (IST)", value=next_slot.time().replace(second=0, microsecond=0), key=f"new_time_{interview['interview_id']}")
                        new_duration = st.number_input("New duration (minutes)", min_value=5, max_value=60, value=20, step=5, key=f"new_duration_{interview['interview_id']}")
                        st.caption("Saving invalidates the old invitation. A new link is created only when you click Send invitation again.")
                        reschedule = st.form_submit_button("Save new slot")
                    if reschedule:
                        reschedule_interview(interview["interview_id"], datetime.combine(new_date, new_time, LOCAL_TIMEZONE), int(new_duration))
                        st.session_state["invitation_notice"] = "New slot saved. Click Send invitation to send a fresh link."
                        st.rerun()
            if st.button(
                "Send invitation", key=f"send_invitation_{interview['interview_id']}",
                type="primary", icon=":material/mail:",
                disabled=interview["status"] != "scheduled" or expired or reason is not None,
            ):
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
