import sqlite3

import streamlit as st

from database import get_jobs, update_job


st.title("Saved job descriptions")
st.caption("Browse your saved jobs and update their titles or descriptions.")
st.page_link("pages/0_Create_Job.py", label="Create a new job", icon="➕")

if "jd_update_message" in st.session_state:
    st.success(st.session_state.pop("jd_update_message"))

try:
    jobs = get_jobs()
except sqlite3.Error:
    st.error("Saved jobs could not be loaded. Please refresh and try again.")
    st.stop()

if not jobs:
    st.info("No job descriptions saved yet. Create a job to see it here.")
    st.stop()

st.caption(f"{len(jobs)} saved job{'s' if len(jobs) != 1 else ''}")
jobs_by_id = {job["job_id"]: job for job in jobs}
job_id = st.selectbox(
    "Choose a job",
    options=list(jobs_by_id),
    format_func=lambda value: f"#{value} · {jobs_by_id[value]['title']}",
    key="saved_job_id",
)
job = jobs_by_id[job_id]

st.subheader(job["title"])
st.caption(f"Job ID: {job_id} · Created: {job['created_at']} UTC")

with st.form(f"edit_job_{job_id}"):
    title = st.text_input("Job title", value=job["title"], key=f"saved_title_{job_id}")
    description = st.text_area(
        "Job description",
        value=job["description"],
        height=420,
        key=f"saved_description_{job_id}",
    )
    st.caption("Saving updates this job only. Review your changes before saving.")
    submitted = st.form_submit_button("Save changes", type="primary")

if submitted:
    try:
        if update_job(job_id, title, description):
            st.session_state["jd_update_message"] = (
                f"Job #{job_id} updated. The saved job title or description has changed."
            )
            st.rerun()
        else:
            st.info("No changes made. This job already contains the same title and description.")
    except ValueError as error:
        st.error(str(error))
    except sqlite3.Error:
        st.error("Changes could not be saved. Please try again.")
