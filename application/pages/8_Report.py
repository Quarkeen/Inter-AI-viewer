import re
import sqlite3
from functools import partial

import streamlit as st

from groq_client import generate
from interview_reports import (
    DIMENSIONS, OUTCOMES, REPORT_VERSION, TOPIC_DIMENSIONS, answer_recording, finished_interviews,
    generate_report, get_report, latest_decision, overview_text, report_source, save_report_decision, source_hash, visible_findings,
)
from report_pdf import duration_text, export_report_pdf, radar_svg, score_text


st.set_page_config(layout="wide")
st.page_link("pages/7_Shortlisted.py", label="Back to shortlisted candidates", icon=":material/arrow_back:")


def evidence_passages(items):
    for item in items:
        st.caption(f"Answer evidence · topic {item['main_number']}")
        st.text(item["text"])


def open_decision(report_id):
    st.session_state[f"report_section_{report_id}"] = "HR decision"


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
        st.info("Generate the interview report to review scores, supporting evidence, and record an HR decision.")
        if st.button("Generate interview report", type="primary", icon=":material/auto_awesome:"):
            with st.status("Assessing the interview…", expanded=True) as progress:
                def model(system, documents, schema):
                    return generate(
                        st.secrets.get("GROQ_API_KEY", ""), st.secrets.get("GROQ_MODEL", "openai/gpt-oss-120b"),
                        system, documents, schema, max_completion_tokens=1536,
                        schema_name="interview_report", purpose="interview_scoring",
                        usage_session_id=session_id, pace_requests=True,
                    )
                report_id = generate_report(session_id, model, st.secrets.get("GROQ_MODEL", "openai/gpt-oss-120b"), progress=lambda label: progress.update(label=label))
                progress.update(label="Report ready", state="complete", expanded=False)
            st.session_state[f"report_section_{report_id}"] = "HR decision"
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
    if decision:
        st.caption(f"HR decision: {decision['outcome']} · {decision['decided_at']} UTC")
    else:
        st.info("Report ready. Please record an HR decision.")
    section = st.segmented_control("Report section", ["Overview", "Answers & evidence", "HR decision"],
                                   default=None if f"report_section_{report['report_id']}" in st.session_state else "Overview",
                                   key=f"report_section_{report['report_id']}", selection_mode="single") or "Overview"
    if section == "Overview":
        chart, summary = st.columns([1, 1.25])
        with chart:
            st.html(radar_svg(result["dimension_scores"]))
        with summary:
            st.write(overview_text(result))
            st.caption("Coverage is limited to the questions asked. An untested skill is not a demonstrated weakness.")
            st.caption(f"Resume score: {score_text(saved_source['resume_score'])} · kept separate from the interview score")
            for label, key in [("Strengths", "strengths"), ("Follow-up areas", "gaps")]:
                st.markdown(f"**{label}**")
                findings = visible_findings(result, key)
                if not findings:
                    st.caption("No additional finding recorded within the assessed topics.")
                for item in findings:
                    st.write("• " + item["text"])
            st.button("Review HR decision", type="primary" if not decision else "secondary", on_click=open_decision, args=(report["report_id"],))
    elif section == "Answers & evidence":
        by_topic = {item["main_number"]: item for item in result["topics"]}
        number = st.selectbox("Main topic", list(range(1, 7)), format_func=lambda value: f"Topic {value}" + (" · not assessed" if value not in by_topic else ""))
        if number not in by_topic:
            st.info("No usable answer was recorded for this topic.")
        else:
            topic = by_topic[number]
            columns = st.columns(4)
            for column, name in zip(columns, TOPIC_DIMENSIONS):
                column.metric(DIMENSIONS[name], score_text(topic["dimensions"][name]["score"]))
            answers, reasoning = st.tabs(["Questions & answers", "Score reasoning"])
            with answers:
                turns = [turn for turn in saved_source["turns"] if turn["main_number"] == number]
                selected = st.selectbox("Answer", [turn["turn_id"] for turn in turns], format_func=lambda value: next(("Main question" if turn["kind"] == "main" else "Follow-up") + f" · Q{turn['sequence']}" for turn in turns if turn["turn_id"] == value))
                turn = next(turn for turn in turns if turn["turn_id"] == selected)
                st.write(turn["question"])
                with st.container(border=True, height=240):
                    st.text(turn["answer_text"] or "No answer recorded.")
                with st.expander("Listen to original answer"):
                    recording = answer_recording(session_id, turn["turn_id"])
                    if recording:
                        st.audio(recording, format="audio/wav")
                    else:
                        st.caption("No recording is available for this answer.")
            with reasoning:
                dimension = st.selectbox("Dimension", TOPIC_DIMENSIONS, format_func=lambda value: DIMENSIONS[value])
                rating = topic["dimensions"][dimension]
                st.write(rating["explanation"])
                evidence_passages(rating["evidence"])
        with st.expander("Role alignment and next-round questions"):
            st.write(result["role_alignment"]["explanation"])
            evidence_passages(result["role_alignment"]["evidence"])
            for question in result["next_round_questions"]:
                st.write("• " + question)
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
