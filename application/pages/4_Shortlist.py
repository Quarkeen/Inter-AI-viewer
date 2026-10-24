import sqlite3
from functools import partial

import streamlit as st

from database import get_jobs
from groq_client import generate
from shortlisting import advance_workflow, get_run, get_run_items, start_run


st.title("Shortlist review")
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
    with st.form("generate_shortlist"):
        threshold = st.number_input("Selection threshold", min_value=0.0, max_value=100.0, value=70.0, step=1.0)
        st.caption("Generating a new shortlist replaces any pending review for this job.")
        submitted = st.form_submit_button("Generate shortlist", type="primary")
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
    st.caption(f"Review started: {run['created_at']} UTC · Threshold: {run['threshold']:g}")
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
        for item in items:
            with st.container(border=True):
                name, score = st.columns([4, 1])
                name.subheader(item["full_name"])
                name.caption(f"Application #{item['application_id']} · {item['email']}")
                score.metric("Resume match", f"{item['score']:g}/100")
                st.write("**Recommendation:** " + ("Proposed" if item["proposed"] else "Not proposed"))
                with st.expander("Score evidence"):
                    for component in item["assessment"]["components"].values():
                        if not component["applicable"]:
                            continue
                        st.write(component["explanation"])
                        for evidence in component["evidence"]:
                            left, right = st.columns(2)
                            left.caption("Resume passage")
                            left.text(evidence["resume_quote"])
                            right.caption("Job requirement")
                            right.text(evidence["jd_quote"])
                    for gap in item["assessment"]["gaps"]:
                        st.write(gap["explanation"])
                        st.text(gap["jd_quote"])
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
