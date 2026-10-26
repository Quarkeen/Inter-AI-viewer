import sqlite3

import streamlit as st

from database import get_jobs, save_application
from resume import extract_resume


def load_resume(): # extracts resume using pypdf
    for key in ("resume_error", "resume_source_text", "application_receipt"):
        st.session_state.pop(key, None)
    uploaded = st.session_state["candidate_resume"]
    if uploaded is None:
        return
    try:
        text = extract_resume(uploaded.getvalue()) # imported from resume.py
        st.session_state["resume_source_text"] = text
    except ValueError as error:
        st.session_state["resume_error"] = str(error)


st.title("Apply for a job")
st.caption("Choose a role, add your details, and upload your resume.")

if "application_receipt" in st.session_state:
    st.success(st.session_state["application_receipt"])
if st.session_state.pop("show_application_alert", False):
    st.toast(st.session_state["application_receipt"], icon="✅")

try:
    jobs = get_jobs()
except sqlite3.Error:
    st.error("Jobs could not be loaded. Please refresh and try again.")
    st.stop()

if not jobs:
    st.info("There are no jobs available yet. Please check back later.")
    st.stop()

jobs_by_id = {job["job_id"]: job for job in jobs}
job_id = st.selectbox(
    "Select a job",
    options=list(jobs_by_id),
    index=None,
    placeholder="Choose the role you want to apply for",
    format_func=lambda value: f"{jobs_by_id[value]['title']} · Job #{value}",
    key="application_job_id",
)
if job_id is not None:
    with st.expander("Read the job description", expanded=True):
        st.text(jobs_by_id[job_id]["description"])

uploaded = st.file_uploader(
    "Upload your resume PDF",
    type=["pdf"],
    max_upload_size=5,
    key="candidate_resume",
    on_change=load_resume,
    help="Up to 5 MB. Use a text-based PDF, not a scanned image.",
)
if st.session_state.get("resume_error"):
    st.error(st.session_state["resume_error"])

source_text = st.session_state.get("resume_source_text", "")
with st.form("candidate_application"):
    full_name = st.text_input("Full name", max_chars=200)
    email = st.text_input("Email address", max_chars=254, placeholder="name@example.com")
    submitted = st.form_submit_button("Apply", type="primary")

if submitted:
    try:
        if job_id is None:
            raise ValueError("Select the job you want to apply for.")
        if uploaded is None or not source_text or st.session_state.get("resume_error"):
            raise ValueError("Upload a readable resume PDF before applying.")
        application_id = save_application(
            job_id, full_name, email, uploaded.name, uploaded.getvalue(), source_text, source_text,
        )
        st.session_state["application_receipt"] = (
            f"Application submitted successfully for {jobs_by_id[job_id]['title']}. "
            f"Your application ID is #{application_id}."
        )
        st.session_state["show_application_alert"] = True
        st.rerun()
    except ValueError as error:
        st.error(str(error))
    except sqlite3.Error:
        st.error("Your application could not be saved. Please try again.")
