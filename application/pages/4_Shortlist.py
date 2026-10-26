import math
import sqlite3
from html import escape
from functools import partial

import streamlit as st

from database import get_jobs
from groq_client import generate
from resume_evidence_ui import show_resume_evidence
from screening import WEIGHTS
from shortlisting import advance_workflow, get_run, get_run_items, start_run


st.set_page_config(layout="wide")
st.title("Shortlist review")
st.html("""<style>
div[class*="st-key-shortlist_row_"] { padding: 12px 16px; border-radius: 12px; }
.review-name { font-weight: 650; font-size: 1rem; overflow-wrap: anywhere; }
.review-meta { font-size: .8rem; opacity: .75; overflow-wrap: anywhere; margin-top: 4px; }
.review-label { font-size: .73rem; opacity: .7; margin-bottom: 4px; }
.review-score { font-size: 1.4rem; font-weight: 650; }
.review-score span { font-size: .8rem; font-weight: 400; opacity: .7; }
</style>""")
st.caption("Generate recommendations, review their evidence, and decide who progresses to interview.")


def advance(run_id, decisions=None):
    return advance_workflow(
        run_id,
        partial(generate, st.secrets.get("GROQ_API_KEY", ""), st.secrets.get("GROQ_MODEL", "openai/gpt-oss-120b")),
        st.secrets.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        decisions=decisions,
    )


try:
    jobs = get_jobs()
    if not jobs:
        st.info("Create a job and receive applications before generating a shortlist.")
        st.stop()
    jobs_by_id = {job["job_id"]: job for job in jobs}
    job_id = st.selectbox(
        "Select a job", list(jobs_by_id),
        format_func=lambda value: f"#{value} · {jobs_by_id[value]['title']}",
        key="shortlist_job_id",
    )
    labels = {"required_skills": "Required skills", "relevant_experience": "Relevant experience",
              "projects": "Projects", "preferred_qualifications": "Preferred qualifications"}
    with st.container(border=True):
        st.markdown("**Resume evaluation criteria**")
        for column, (name, weight) in zip(st.columns(len(WEIGHTS)), WEIGHTS.items()):
            column.caption(labels[name])
            column.markdown(f"**{weight}%**")
        st.caption("Each applicable category must meet your selection threshold. Preferred qualifications are excluded when the JD has none.")
    with st.form("generate_shortlist"):
        threshold_column, explanation, generate_column = st.columns([1.2, 4, 1.5], vertical_alignment="bottom")
        with threshold_column:
            threshold = st.number_input("Selection threshold", min_value=0.0, max_value=100.0, value=70.0, step=1.0, format="%.0f")
        with explanation:
            st.caption("Generating a new shortlist replaces any pending review for this job.")
        with generate_column:
            submitted = st.form_submit_button("Generate shortlist", type="primary", width="stretch")
    if submitted:
        run_id = start_run(job_id, threshold)
        try:
            with st.spinner("Evaluating applications and preparing recommendations…"):
                advance(run_id)
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        else:
            st.rerun()

    run = get_run(job_id=job_id)
    if not run:
        st.info("Choose a threshold and generate the first shortlist for this job.")
        st.stop()
    if run["status"] == "evaluating":
        st.info("This run has not finished evaluating. Continue to reuse completed results and finish the remaining applications.")
        if st.button("Continue evaluation"):
            with st.spinner("Continuing evaluation…"):
                advance(run["run_id"])
            st.rerun()
        st.stop()

    items = get_run_items(run["run_id"])
    if run["status"] == "completed":
        approved_count = sum(item["approved"] == 1 for item in items)
        st.success(f"HR decisions saved. {approved_count} application(s) approved for interview.")
        st.page_link("pages/5_Schedule.py", label="Schedule approved applicants", icon="📅")
    else:
        st.info("Paused for HR review. Check the applications you approve, then save your decisions.")
    st.caption(f"{sum(item['proposed'] for item in items)} proposed · {len(items)} applications reviewed")

    decisions = []
    with st.form(f"review_{run['run_id']}"):
        with st.container(height=480, border=False):
            for item in items:
                with st.container(border=True, key=f"shortlist_row_{run['run_id']}_{item['application_id']}"):
                    identity, score, recommendation, evidence_column, approval = st.columns(
                        [3, 1.1, 1.5, 1.1, 2], gap="small", vertical_alignment="center",
                    )
                    with identity:
                        st.html(f'<div class="review-name">{escape(item["full_name"])}</div><div class="review-meta">#{item["application_id"]} · {escape(item["email"])}</div>')
                    with score:
                        st.html(f'<div class="review-label">RESUME MATCH</div><div class="review-score">{math.floor(item["score"] + .5)}<span> / 100</span></div>')
                    with recommendation:
                        st.caption("AI RECOMMENDATION")
                        st.write("Proposed" if item["proposed"] else "Not proposed")
                    with evidence_column:
                        with st.popover("Evidence", width="stretch"):
                            show_resume_evidence(item["assessment"])
                    with approval:
                        approved = st.checkbox(
                            "Approve for interview",
                            value=bool(item["approved"]) if run["status"] == "completed" else bool(item["proposed"]),
                            disabled=run["status"] == "completed",
                            key=f"approve_{run['run_id']}_{item['application_id']}",
                        )
                    decisions.append({"application_id": item["application_id"], "approved": approved})
        save = st.form_submit_button("Save HR decisions", type="primary", disabled=run["status"] == "completed")
    if save:
        advance(run["run_id"], decisions)
        st.rerun()

except (ValueError, RuntimeError) as error:
    st.error(str(error))
except FileNotFoundError:
    st.error("Configure the local Groq secrets file before generating a shortlist.")
except sqlite3.Error:
    st.error("The shortlist could not be loaded or saved. Please try again.")
