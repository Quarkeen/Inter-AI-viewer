import re
import sqlite3
from functools import partial
from datetime import datetime

import streamlit as st

from interview_reports import (
    OUTCOMES, REPORT_VERSION, answer_recording, finished_interviews,
    get_report, latest_decision, overview_text, report_source, save_report_decision, source_hash, visible_findings,
)
from report_pdf import duration_text, export_report_pdf, radar_svg, score_text
from report_jobs import report_job_status, start_report_job
from scheduling import utc_now


st.set_page_config(layout="wide")
st.html("""<style>
.stMainBlockContainer { max-width: 1100px; margin-inline: auto; }
div.st-key-report_question { border-radius: 12px; padding: 20px; }
</style>""")
st.page_link("pages/7_Shortlisted.py", label="Back to shortlisted candidates", icon=":material/arrow_back:")


def open_decision(report_id):
    st.session_state[f"report_section_{report_id}"] = "HR decision"


def generation_running(report, job):
    lease_active = report and report["generation_owner"] and report["lease_until"] and datetime.fromisoformat(report["lease_until"]) > utc_now()
    return job["state"] in ("queued", "running") or bool(lease_active)


@st.fragment(run_every="3s")
def report_progress(session_id):
    saved = get_report(session_id=session_id)
    job = report_job_status(session_id)
    if saved and saved["status"] == "ready":
        st.rerun()
    if not generation_running(saved, job):
        st.rerun()
    st.info("Your report is being prepared. You can visit another page and return here; generation will continue.")
    topics = {topic["main_number"] for key, values in (saved["parts"] if saved else {}).items()
              if key.startswith("topics_") for topic in values}
    if topics:
        st.caption(f"{len(topics)} topics assessed and saved. Preparing the remaining assessment.")
    else:
        st.caption("Waiting for the assessment to begin." if job["state"] == "queued" else "Assessing the recorded answers. Requests are paced to respect model rate limits.")


try:
    sessions = finished_interviews()
    if not sessions:
        st.title("Candidate report")
        st.info("Reports become available after an interview has finished with recorded answers.")
        st.stop()
    choices = {item["session_id"]: item for item in sessions}
    requested = st.query_params.get("session")
    session_id = requested if requested in choices else None
    if requested and not session_id:
        st.error("This interview report is unavailable.")
        st.stop()
    if not session_id:
        session_id = st.selectbox("Interview", list(choices), format_func=lambda value: f"{choices[value]['full_name']} · #{choices[value]['application_id']} · {choices[value]['role']}")
    source = report_source(session_id)
    report = get_report(session_id=session_id)
    current = report and report["status"] == "ready" and report["rubric_version"] == REPORT_VERSION and report["source_sha256"] == source_hash(source)
    st.title(source["full_name"])
    st.caption(f"{source['role']} · Application #{source['application_id']} · Interview #{source['interview_id']}")
    if not current:
        job = report_job_status(session_id)
        if generation_running(report, job):
            report_progress(session_id)
            st.stop()
        if job["state"] == "failed":
            st.error(job["message"])
        resumable = report and report["status"] == "generating"
        if resumable:
            st.info("Generation was interrupted. Your completed assessments are saved; resume to finish the report.")
        else:
            st.info("Generate the interview report to review scores, supporting evidence, and record an HR decision.")
        if st.button("Resume report generation" if resumable else "Generate interview report", type="primary", icon=":material/auto_awesome:"):
            start_report_job(session_id, st.secrets.get("GROQ_API_KEY", ""), st.secrets.get("GROQ_MODEL", "openai/gpt-oss-120b"))
            st.rerun()
        st.stop()

    result, saved_source = report["assessment"], report["source"]
    decision = latest_decision(report["report_id"])
    scores = st.columns([1, 1, 1, 1.4])
    scores[0].metric("Interview score", score_text(result["overall_score"]))
    scores[1].metric("Topics assessed", f"{result['coverage']}/6")
    scores[2].metric("Duration", duration_text(saved_source))
    filename = re.sub(r"[^a-zA-Z0-9_-]", "_", saved_source["full_name"])
    with scores[3]:
        st.download_button("Download PDF", data=partial(export_report_pdf, report["report_id"]),
                           file_name=f"{filename}_application_{saved_source['application_id']}_report.pdf", mime="application/pdf", icon=":material/download:")
    if saved_source["status"] != "completed":
        st.warning("This interview ended before completion. Untested topics are not scored.")
    if not decision:
        st.info("Report ready. Please record an HR decision.")
    section = st.segmented_control("Report section", ["Overview", "Answers & evidence", "HR decision"],
                                   default=None if f"report_section_{report['report_id']}" in st.session_state else "Overview",
                                   key=f"report_section_{report['report_id']}", selection_mode="single") or "Overview"
    if section == "Overview":
        _, chart, _ = st.columns([1, 2, 1])
        with chart:
            # Serve SVG as an image so the pentagon is rendered reliably.
            svg = radar_svg(result["dimension_scores"]).replace('fill="currentColor"', 'fill="#173649"')
            opening, shapes = svg.split(">", 1)
            st.image(opening + '><rect width="500" height="350" rx="12" fill="#f5f8fa"/>' + shapes, width="stretch")
        st.write(overview_text(result))
        for column, (label, key) in zip(st.columns(2), [("Strengths", "strengths"), ("Follow-up areas", "gaps")]):
            with column.container(border=True):
                st.markdown(f"**{label}**")
                findings = visible_findings(result, key)
                if not findings:
                    st.caption("No additional finding within the assessed topics.")
                for item in findings[:2]:
                    st.write("• " + item["text"])
        st.button("Review HR decision", type="primary" if not decision else "secondary", on_click=open_decision, args=(report["report_id"],))
    elif section == "Answers & evidence":
        turns = {turn["turn_id"]: turn for turn in saved_source["turns"]}
        if not turns:
            st.info("No questions are available for this interview.")
        else:
            selected = st.selectbox(
                "Question", list(turns), key=f"report_question_{report['report_id']}",
                format_func=lambda value: f"Q{turns[value]['sequence']} · {turns[value]['question']}",
            )
            turn = turns[selected]
            with st.container(border=True, key="report_question"):
                st.caption("QUESTION")
                st.write(turn["question"])
                st.caption("TRANSCRIBED ANSWER")
                answer = turn["answer_text"] or "No answer recorded."
                if len(answer) > 1000:
                    with st.container(height=180, border=False):
                        st.text(answer)
                else:
                    st.text(answer)
                st.caption("ORIGINAL AUDIO")
                recording = answer_recording(session_id, turn["turn_id"])
                if recording:
                    st.audio(recording, format="audio/wav")
                else:
                    st.caption("No recording is available for this answer.")
    else:
        st.subheader("HR decision")
        st.caption("Review the evidence, then choose the application's next step.")
        with st.form(f"report_decision_{report['report_id']}"):
            outcome = st.selectbox("Outcome", OUTCOMES, index=OUTCOMES.index(decision["outcome"]) if decision else None, placeholder="Choose an outcome")
            notes = st.text_area("Internal note (optional)", value=decision["notes"] if decision else "", max_chars=1000, height=80)
            save = st.form_submit_button("Save HR decision", type="primary")
        if save:
            save_report_decision(report["report_id"], outcome, notes)
            st.success("HR decision saved.")
            st.rerun()
except (ValueError, RuntimeError) as error:
    st.error(str(error))
except FileNotFoundError:
    st.error("Configure the local model settings before generating reports.")
except sqlite3.Error:
    st.error("The report could not be loaded or saved. Please try again.")
