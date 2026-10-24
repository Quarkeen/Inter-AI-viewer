import sqlite3
from io import BytesIO

import streamlit as st
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from database import save_job


def load_description():
    st.session_state.pop("upload_error", None)
    uploaded_file = st.session_state["jd_file"]
    if uploaded_file is None:
        return

    try:
        if uploaded_file.size > 5 * 1024 * 1024:
            raise ValueError("Upload a file smaller than 5 MB.")

        content = uploaded_file.getvalue()
        if uploaded_file.name.lower().endswith(".pdf"):
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise ValueError("Upload an unencrypted PDF.")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            text = content.decode("utf-8-sig")

        if not text.strip():
            raise ValueError("No text was found. Paste the description instead.")
        st.session_state["job_description"] = text.strip()
    except UnicodeDecodeError:
        st.session_state["upload_error"] = "Use a UTF-8 text file or paste the description."
    except PdfReadError:
        st.session_state["upload_error"] = "Could not read this PDF. Try another file or paste the description."
    except ValueError as error:
        st.session_state["upload_error"] = str(error)


st.title("HR Workspace")
st.page_link("pages/1_Saved_JDs.py", label="View and edit saved JDs", icon="📋")
st.subheader("Create a job")

st.file_uploader(
    "Upload a job description (optional)",
    type=["pdf", "txt"],
    key="jd_file",
    on_change=load_description,
)
st.caption("You can also type or paste below. Review uploaded text before saving.")

if st.session_state.get("upload_error"):
    st.error(st.session_state["upload_error"])

with st.form("create_job"):
    title = st.text_input("Job title")
    description = st.text_area("Job description", key="job_description", height=250)
    submitted = st.form_submit_button("Save job", type="primary")

if submitted:
    try:
        job_id = save_job(title, description)
        st.success(f"Job #{job_id} saved: {title.strip()}")
    except ValueError as error:
        st.error(str(error))
    except sqlite3.Error:
        st.error("The job could not be saved. Please try again.")
