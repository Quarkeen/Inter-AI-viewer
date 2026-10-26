import sqlite3
from datetime import datetime

import streamlit as st

from groq_client import generate
from interview_agent import PLAN_SCHEMA, continue_interview
from interview_store import COMPLETION_MESSAGE, get_session, get_turns, start_session
from interview_ui import ROOM_CSS, question_card, thinking
from invitations import format_slot
from scheduling import get_invitation_access, utc_now


st.html(ROOM_CSS)
token = st.query_params.get("token", "")


def run_action(recording=None, turn_id=None):
    placeholder = st.empty()
    transcript_area = st.empty()
    thinking(placeholder, "Preparing your next question")

    def stage(name, transcript):
        thinking(placeholder, "Transcribing your answer" if name == "transcribing" else "Preparing your next question")
        if transcript:
            with transcript_area.container(border=True):
                st.caption("YOUR ANSWER")
                st.text(transcript)

    def ask_model(system, documents, schema):
        session = get_session(token)
        return generate(
            st.secrets.get("GROQ_API_KEY", ""), st.secrets.get("GROQ_MODEL", "openai/gpt-oss-120b"),
            system, documents, schema, max_completion_tokens=1536 if schema == PLAN_SCHEMA else 1024,
            schema_name="interview_question", interview_id=session["session_id"],
        )

    try:
        with st.spinner("Please wait…"):
            continue_interview(token, ask_model, recording=recording, turn_id=turn_id, on_stage=stage)
    except (ValueError, RuntimeError) as error:
        st.session_state["interview_notice"] = str(error)
    except Exception:
        st.session_state["interview_notice"] = "The interviewer could not continue. Your saved progress is safe; please retry."
    finally:
        placeholder.empty()
    st.rerun()


@st.fragment(run_every="3s")
def slot_clock():
    session = get_session(token)
    if session is None or session["status"] != "active":
        st.rerun()
    seconds = max(0, int((datetime.fromisoformat(session["slot_end"]) - utc_now()).total_seconds()))
    st.metric("Time remaining", f"{seconds // 60:02d}:{seconds % 60:02d}")


@st.fragment(run_every="15s")
def waiting_room():
    access = get_invitation_access(token)
    if access["state"] != "early":
        st.rerun()
    st.info("Your interview has not started yet.")
    st.write(f"Your slot opens at **{format_slot(access['opens_at'])}**.")
    st.caption("This page checks your slot automatically. Keep your invitation private.")


try:
    access = get_invitation_access(token)
    if access["state"] == "rescheduled":
        st.title("Your interview has been rescheduled")
        st.info("This invitation link is no longer active. You will receive updated interview details and a new link by email.")
        st.caption("Please use the latest invitation from the recruitment team. Contact them if you have not received the updated email.")
        st.stop()
    if access["state"] == "invalid":
        st.title("Interview invitation")
        st.error("This invitation is unavailable. Please contact the hiring team.")
        st.stop()
    session = get_session(token)
    if session and session["status"] in ("completed", "expired"):
        st.title("Thank you for your time")
        st.success(COMPLETION_MESSAGE)
        st.stop()
    if access["state"] == "early":
        st.title("Your interview is scheduled")
        waiting_room()
        st.stop()
    if access["state"] == "expired":
        st.title("Interview invitation")
        st.warning("The window to start this interview has closed.")
        st.caption("Please contact the hiring team if you need help with your invitation.")
        st.stop()

    st.html('<div class="interview-eyebrow">INTERVIEW ROOM</div>')
    if not session:
        st.title("Ready when you are")
        st.write("You will discuss your skills and projects with an AI interviewer.")
        with st.container(border=True):
            st.write("**Six main questions · up to four follow-ups**")
            st.write("Listen to or read each question, then tap the microphone to start and stop your answer. Submit the recording when you are ready.")
            st.caption("Keep each answer under three minutes. Your transcribed answers will appear here automatically.")
            st.write(f"Start between **{format_slot(access['opens_at'])}** and **{format_slot(access['closes_at'])}**.")
            st.caption(f"You have {access['duration_minutes']} minutes from when you click Start interview. Refreshing does not reset your timer.")
        if st.button("Start interview", type="primary", icon=":material/play_arrow:"):
            start_session(token)
            run_action()
        st.stop()

    st.title(session["job_title"])
    st.caption(f"Welcome, {session['full_name']}. Take a moment to think before you answer.")
    turns = get_turns(session["session_id"])
    progress, timer = st.columns([3, 1])
    with progress:
        mains_answered = sum(turn["kind"] == "main" and turn["answer_text"] is not None for turn in turns)
        follows = sum(turn["kind"] == "follow_up" for turn in turns)
        st.progress(mains_answered / 6, text=f"Main questions answered: {mains_answered}/6 · Follow-ups: {follows}/4")
    with timer:
        slot_clock()
    if "interview_notice" in st.session_state:
        st.warning(st.session_state.pop("interview_notice"))
    if not turns:
        if st.button("Prepare first question", type="primary"):
            run_action()
        st.stop()

    current = turns[-1]
    if current["answer_text"] is None:
        st.caption(f"MAIN QUESTION {current['main_number']} OF 6" if current["kind"] == "main" else f"FOLLOW-UP · TOPIC {current['main_number']}")
        read_aloud = st.toggle("Read questions aloud", value=True, key="interview_read_aloud")
        question_card(current["question"], current["turn_id"], autoplay=read_aloud)
        with st.container(border=True):
            st.subheader("Your answer")
            st.caption("Let the question finish speaking before recording. Tap the microphone to start, then tap stop when finished.")
            recording = st.audio_input("Record your answer", sample_rate=16000, key=f"interview_audio_{current['turn_id']}")
            if st.button("Submit answer", type="primary", disabled=recording is None, key=f"submit_answer_{current['turn_id']}"):
                run_action(recording.getvalue(), current["turn_id"])
    else:
        with st.container(border=True):
            st.caption("YOUR ANSWER")
            st.text(current["answer_text"])
        if st.button("Continue interview", type="primary"):
            run_action()

    answered = [turn for turn in turns if turn["answer_text"]]
    if answered:
        if current["answer_text"] is None:
            with st.container(border=True):
                st.caption("YOUR LAST ANSWER")
                st.text(answered[-1]["answer_text"])
        with st.expander("Conversation so far"):
            for turn in answered:
                st.markdown(f"**Question {turn['sequence']}**")
                st.text(turn["question"])
                st.caption("YOUR ANSWER")
                st.text(turn["answer_text"])
except (ValueError, RuntimeError) as error:
    st.error(str(error))
except sqlite3.Error:
    st.error("Your interview could not be loaded. Please refresh and try again.")
