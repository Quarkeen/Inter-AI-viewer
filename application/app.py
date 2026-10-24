import streamlit as st

from database import init_database


st.set_page_config(page_title="HR Workspace", layout="centered")
init_database()

page = st.navigation({
    "HR": [
        st.Page("pages/0_Create_Job.py", title="Create a job", icon="➕", default=True),
        st.Page("pages/1_Saved_JDs.py", title="Saved JDs", icon="📋", url_path="saved-jds"),
        st.Page("pages/3_Applications.py", title="Applications", icon="👥", url_path="applications"),
        st.Page("pages/4_Shortlist.py", title="Shortlist review", icon="✅", url_path="shortlist"),
        st.Page("pages/5_Schedule.py", title="Interview scheduling", icon="📅", url_path="schedule"),
        st.Page("pages/7_Shortlisted.py", title="Shortlisted candidates", icon="📊", url_path="shortlisted"),
        st.Page("pages/8_Report.py", title="Candidate report", url_path="candidate-report", visibility="hidden"),
    ],
    "Candidate": [
        st.Page("pages/2_Apply.py", title="Apply for a job", icon="📝", url_path="apply"),
        st.Page("pages/6_Interview.py", title="Interview invitation", url_path="interview", visibility="hidden"),
    ],
}, position="hidden" if st.query_params.get("token") else "sidebar")
page.run()
