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
from screening import RUBRIC_VERSION, assess_resume, document_hashes


st.title("Candidate applications")
st.caption("See who applied for each role and download their original resume PDF.")

st.html("""
<style>
div[class*="st-key-application_card_"] {
    padding: 1.4rem;
    border-radius: 16px;
}
.applicant-heading { display: flex; align-items: center; gap: 14px; min-width: 0; }
.applicant-avatar {
    flex: 0 0 48px; height: 48px; display: grid; place-items: center;
    border-radius: 14px; background: #173b40; color: #a6e5d9;
    font-size: 1rem; font-weight: 650; letter-spacing: .04em;
}
.applicant-identity { min-width: 0; }
.applicant-name {
    margin: 0; color: var(--text-color); font-size: 1.3rem;
    line-height: 1.35; font-weight: 650; overflow-wrap: anywhere;
}
.applicant-email {
    display: inline-block; margin-top: 4px; font-size: .88rem;
    color: var(--text-color); opacity: .7; overflow-wrap: anywhere;
    text-decoration: none;
}
.applicant-email:hover { opacity: 1; text-decoration: underline; }
.application-role {
    margin-top: 18px; display: inline-block; padding: 6px 11px;
    border-radius: 7px; background: var(--secondary-background-color);
    color: var(--text-color); font-size: .87rem; font-weight: 500;
    overflow-wrap: anywhere;
}
.application-meta {
    display: grid; grid-template-columns: 1fr 1fr 2fr; gap: 16px;
    padding: 18px 0; margin-top: 2px;
}
.application-meta-label {
    display: block; font-size: .7rem; text-transform: uppercase;
    letter-spacing: .08em; opacity: .55; margin-bottom: 5px;
}
.application-meta-value { font-size: .85rem; font-weight: 500; }
@media (max-width: 600px) {
    div[class*="st-key-application_card_"] { padding: 1rem; }
    .application-meta { grid-template-columns: 1fr 1fr; }
    .application-date { grid-column: 1 / -1; }
}
</style>
""")

try:
    jobs = get_jobs()
    jobs_by_id = {job["job_id"]: job for job in jobs}
    job_id = st.selectbox(
        "Filter by job",
        options=[None, *jobs_by_id],
        format_func=lambda value: "All jobs" if value is None else f"#{value} · {jobs_by_id[value]['title']}",
    )
    applications = get_applications(job_id)
except sqlite3.Error:
    st.error("Applications could not be loaded. Please refresh and try again.")
    st.stop()

if not applications:
    st.info("No applications received for this selection yet.")
    st.stop()

st.caption(f"{len(applications)} application{'s' if len(applications) != 1 else ''}")
for application in applications:
    initials = "".join(part[0] for part in application["full_name"].split()[:2]).upper()
    applied_at = datetime.fromisoformat(application["applied_at"]).strftime("%d %b %Y · %H:%M UTC")
    with st.container(border=True, key=f"application_card_{application['application_id']}"):
        details, download = st.columns([5, 2], vertical_alignment="center")
        with details:
            st.html(f"""
                <div class="applicant-heading">
                    <div class="applicant-avatar" aria-hidden="true">{escape(initials)}</div>
                    <div class="applicant-identity">
                        <h3 class="applicant-name">{escape(application['full_name'])}</h3>
                        <a class="applicant-email" href="mailto:{escape(quote(application['email'], safe='@.+'))}">
                            {escape(application['email'])}
                        </a>
                    </div>
                </div>
            """)
        with download:
            st.download_button(
                "Download resume",
                data=partial(get_resume_pdf, application["application_id"]),
                file_name=application["resume_filename"],
                mime="application/pdf",
                key=f"resume_download_{application['application_id']}",
                on_click="ignore",
                icon=":material/download:",
                width="stretch",
                help="Download the candidate's original PDF resume",
            )
        st.html(f"""
            <div class="application-role">{escape(application['job_title_at_application'])}</div>
            <div class="application-meta">
                <div>
                    <span class="application-meta-label">Application</span>
                    <span class="application-meta-value">#{application['application_id']}</span>
                </div>
                <div>
                    <span class="application-meta-label">Job reference</span>
                    <span class="application-meta-value">#{application['job_id']}</span>
                </div>
                <div class="application-date">
                    <span class="application-meta-label">Date applied</span>
                    <span class="application-meta-value">{escape(applied_at)}</span>
                </div>
            </div>
        """)
        if application["current_job_title"] != application["job_title_at_application"]:
            st.caption(f"Current job title: {application['current_job_title']}")

        try:
            evaluation = get_latest_resume_evaluation(application["application_id"])
            resume_hash, jd_hash = document_hashes(
                application["resume_text"], application["job_title_at_application"], application["jd_at_application"],
            )
            if evaluation and (
                evaluation["resume_sha256"] != resume_hash
                or evaluation["jd_sha256"] != jd_hash
                or evaluation["rubric_version"] != RUBRIC_VERSION
            ):
                evaluation = None

            score_column, action_column = st.columns([3, 2], vertical_alignment="center")
            with score_column:
                if evaluation:
                    st.metric("Resume match", f"{evaluation['score']:g}/100")
                    st.caption(f"Evaluated: {evaluation['evaluated_at']} UTC")
                else:
                    st.caption("Not evaluated yet")
            with action_column:
                evaluate = st.button(
                    "Re-evaluate resume" if evaluation else "Evaluate resume",
                    key=f"evaluate_{application['application_id']}",
                    icon=":material/auto_awesome:",
                    width="stretch",
                )
            if evaluate:
                api_key = st.secrets.get("GROQ_API_KEY", "")
                model_name = st.secrets.get("GROQ_MODEL", "openai/gpt-oss-120b")
                with st.spinner("Comparing the resume with this application's job description…"):
                    source = get_evaluation_input(application["application_id"])
                    result = assess_resume(
                        source["resume_text"], source["job_title_at_application"], source["jd_at_application"],
                        partial(generate, api_key, model_name),
                    )
                    save_resume_evaluation(
                        application["application_id"], source["resume_text"],
                        source["job_title_at_application"], source["jd_at_application"],
                        result["assessment"], model_name,
                    )
                st.rerun()

            if evaluation:
                with st.expander("Score evidence", icon=":material/fact_check:"):
                    st.caption("Based on the submitted resume and the job description saved with this application.")
                    for component in evaluation["assessment"]["components"].values():
                        if not component["applicable"]:
                            continue
                        st.write(component["explanation"])
                        for item in component["evidence"]:
                            resume_column, jd_column = st.columns(2)
                            with resume_column:
                                st.caption("Resume passage")
                                st.text(item["resume_quote"])
                            with jd_column:
                                st.caption("Job requirement")
                                st.text(item["jd_quote"])
                    if evaluation["assessment"]["gaps"]:
                        st.markdown("**Not demonstrated in the resume**")
                        for gap in evaluation["assessment"]["gaps"]:
                            st.write(gap["explanation"])
                            st.text(gap["jd_quote"])
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        except FileNotFoundError:
            st.error("Configure the local Groq secrets file before evaluating resumes.")
        except sqlite3.Error:
            st.error("The evaluation could not be loaded or saved. Please try again.")
