import math
import sqlite3
from datetime import datetime
from functools import partial
from html import escape
from urllib.parse import quote

import streamlit as st

from database import (
    get_applications, get_evaluation_input, get_jobs, get_latest_resume_evaluation,
    get_resume_pdf, save_resume_evaluation,
)
from groq_client import generate
from resume_evidence_ui import show_resume_evidence
from screening import RUBRIC_VERSION, assess_resume, document_hashes


st.set_page_config(layout="wide")
st.title("Candidate applications")
st.caption("Review applicants, compare resume matches and open the evidence when needed.")
st.html("""
<style>
div[class*="st-key-application_row_"] { border-radius: 12px; padding: 14px 18px; }
.applicant-heading { display: flex; align-items: center; gap: 12px; min-width: 0; }
.applicant-avatar {
    flex: 0 0 38px; height: 38px; display: grid; place-items: center;
    border-radius: 10px; background: #173b40; color: #a6e5d9;
    font-size: .8rem; font-weight: 650;
}
.applicant-identity { min-width: 0; }
.applicant-name { font-size: 1rem; font-weight: 650; overflow-wrap: anywhere; }
.applicant-email {
    display: block; margin-top: 3px; font-size: .8rem;
    color: var(--text-color); opacity: .75; overflow-wrap: anywhere; text-decoration: none;
}
.applicant-email:hover { text-decoration: underline; }
.application-label { font-size: .73rem; opacity: .7; margin-bottom: 4px; }
.application-role { font-size: .9rem; font-weight: 500; overflow-wrap: anywhere; }
.application-score { font-size: 1.45rem; font-weight: 650; line-height: 1.2; }
.application-score span { font-size: .8rem; font-weight: 400; opacity: .7; }
@media (max-width: 640px) {
    div[class*="st-key-application_row_"] { padding: 12px; }
}
</style>
""")

try:
    jobs_by_id = {job["job_id"]: job for job in get_jobs()}
    job_filter, search = st.columns([1, 1.4])
    with job_filter:
        job_id = st.selectbox(
            "Filter by job", options=[None, *jobs_by_id],
            format_func=lambda value: "All jobs" if value is None else f"#{value} · {jobs_by_id[value]['title']}",
        )
    with search:
        query = st.text_input("Search candidates", placeholder="Search by name or email").strip().casefold()
    applications = get_applications(job_id)
except sqlite3.Error:
    st.error("Applications could not be loaded. Please refresh and try again.")
    st.stop()

applications = [item for item in applications if not query or
                query in item["full_name"].casefold() or query in item["email"].casefold()]
criteria = (job_id, query)
if st.session_state.get("applications_filter") != criteria:
    st.session_state.applications_filter = criteria
    st.session_state.applications_page = 0

if not applications:
    st.info("No applications match this selection.")
    st.stop()

page_size = 6
pages = math.ceil(len(applications) / page_size)
page = min(st.session_state.get("applications_page", 0), pages - 1)
summary, previous, following = st.columns([6, 1, 1])
with previous:
    if st.button("Previous", disabled=page == 0, width="stretch"):
        st.session_state.applications_page = page - 1
        st.rerun()
with following:
    if st.button("Next", disabled=page == pages - 1, width="stretch"):
        st.session_state.applications_page = page + 1
        st.rerun()
st.session_state.applications_page = page
start = page * page_size
with summary:
    noun = "application" if len(applications) == 1 else "applications"
    st.caption(f"{len(applications)} {noun} · Showing {start + 1}–{min(start + page_size, len(applications))} · Page {page + 1} of {pages}")

for application in applications[start:start + page_size]:
    app_id = application["application_id"]
    initials = "".join(part[0] for part in application["full_name"].split()[:2]).upper()
    applied_at = datetime.fromisoformat(application["applied_at"]).strftime("%d %b %Y")
    with st.container(border=True, key=f"application_row_{app_id}"):
        try:
            evaluation = get_latest_resume_evaluation(app_id)
            resume_hash, jd_hash = document_hashes(
                application["resume_text"], application["job_title_at_application"], application["jd_at_application"],
            )
            if evaluation and (
                evaluation["resume_sha256"] != resume_hash
                or evaluation["jd_sha256"] != jd_hash
                or evaluation["rubric_version"] != RUBRIC_VERSION
            ):
                evaluation = None

            candidate, role, score, evidence, download = st.columns(
                [3.2, 2.8, 1.3, 1.1, 1.2], gap="small", vertical_alignment="center",
            )
            with candidate:
                st.html(f"""
                    <div class="applicant-heading">
                        <div class="applicant-avatar" aria-hidden="true">{escape(initials)}</div>
                        <div class="applicant-identity">
                            <div class="applicant-name">{escape(application['full_name'])}</div>
                            <a class="applicant-email" href="mailto:{escape(quote(application['email'], safe='@.+'))}">{escape(application['email'])}</a>
                        </div>
                    </div>
                """)
                st.caption(f"#{app_id} · Applied {applied_at}")
            with role:
                st.html(f'<div class="application-label">APPLIED FOR</div><div class="application-role">{escape(application["job_title_at_application"])}</div>')
                if application["current_job_title"] != application["job_title_at_application"]:
                    st.caption(f"Current title: {application['current_job_title']}")
            with score:
                evaluate = False
                if evaluation:
                    st.html(f'<div class="application-label">RESUME MATCH</div><div class="application-score">{math.floor(evaluation["score"] + .5)}<span> / 100</span></div>')
                else:
                    st.caption("Not evaluated")
                    evaluate = st.button(
                        "Evaluate", key=f"evaluate_{app_id}", width="stretch",
                        help="Compare this resume with its job description using AI.",
                    )
            with evidence:
                with st.popover("Evidence", disabled=not evaluation, width="stretch"):
                    if evaluation:
                        show_resume_evidence(evaluation["assessment"])
            with download:
                st.download_button(
                    "Resume", data=partial(get_resume_pdf, app_id),
                    file_name=application["resume_filename"], mime="application/pdf",
                    key=f"resume_download_{app_id}", on_click="ignore", icon=":material/download:",
                    width="stretch", help="Download the original resume PDF",
                )
            if evaluate:
                api_key = st.secrets.get("GROQ_API_KEY", "")
                model_name = st.secrets.get("GROQ_MODEL", "openai/gpt-oss-120b")
                with st.spinner("Comparing the resume with this application's job description…"):
                    source = get_evaluation_input(app_id)
                    result = assess_resume(
                        source["resume_text"], source["job_title_at_application"], source["jd_at_application"],
                        partial(generate, api_key, model_name),
                    )
                    save_resume_evaluation(
                        app_id, source["resume_text"], source["job_title_at_application"], source["jd_at_application"],
                        result["assessment"], model_name,
                    )
                st.rerun()
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        except FileNotFoundError:
            st.error("Configure the Groq API key in Streamlit secrets before evaluating resumes.")
        except sqlite3.Error:
            st.error("The evaluation could not be loaded or saved. Please try again.")
