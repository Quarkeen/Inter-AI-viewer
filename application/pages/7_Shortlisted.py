import math
import sqlite3

import streamlit as st

from interview_reports import get_report, latest_decision, rounded_score, shortlisted_candidates
from invitations import format_slot


st.set_page_config(layout="wide")
st.title("Shortlisted candidates")
st.caption("Track approved applicants, open their interview reports, and review HR decisions.")

try:
    candidates = shortlisted_candidates()
    if not candidates:
        st.info("Approved applicants will appear here after HR saves shortlist decisions.")
        st.stop()
    totals = st.columns(4)
    totals[0].metric("Shortlisted", len(candidates))
    for column, status in zip(totals[1:], ("Done", "Ongoing", "Missed")):
        column.metric(status, sum(item["interview_status"] == status for item in candidates))
    left, right = st.columns([3, 2])
    roles = {item["job_id"]: item["role"] for item in candidates}
    job = left.selectbox("Role", [None, *roles], format_func=lambda value: "All roles" if value is None else f"#{value} · {roles[value]}")
    status = right.selectbox("Interview status", ["All", "Done", "Ongoing", "Missed", "Scheduled", "Not scheduled"])
    filtered = [item for item in candidates if (job is None or item["job_id"] == job) and (status == "All" or item["interview_status"] == status)]
    if not filtered:
        st.info("No applicants match this selection.")
        st.stop()
    page_count = math.ceil(len(filtered)/8)
    page = st.selectbox("Page", list(range(1, page_count+1)), label_visibility="collapsed") if page_count > 1 else 1
    rows = []
    symbols = {"Done": "🟢", "Ongoing": "🟡", "Missed": "🔴", "Scheduled": "🕒", "Not scheduled": "⚪"}
    for item in filtered[(page-1)*8:page*8]:
        label = item["interview_status"]
        if label == "Scheduled":
            label = "Scheduled at " + format_slot(item["starts_at"])
        elif label == "Missed" and item["session_id"]:
            label += " · incomplete"
        elif label == "Missed" and item["invitation_status"] != "sent":
            label += " · invitation not confirmed sent"
        report = get_report(item["report_id"]) if item["report_id"] else None
        decision = latest_decision(item["report_id"]) if report else None
        can_report = item["session_id"] and item["has_answers"] and item["interview_status"] in ("Done", "Missed")
        rows.append({
            "Candidate": f"{item['full_name']} · #{item['application_id']}",
            "Role": item["role"], "Interview": symbols[item["interview_status"]] + " " + label,
            "Interview score": rounded_score(report["overall_score"]) if report else None,
            "HR decision": decision["outcome"] if decision else "Pending" if report else "—",
            "Report": f"/candidate-report?session={item['session_id']}" if can_report else None,
        })
    st.dataframe(
        rows, hide_index=True, width="stretch", height=45+35*len(rows),
        column_config={
            "Candidate": st.column_config.TextColumn(width=180),
            "Role": st.column_config.TextColumn(width=220),
            "Interview": st.column_config.TextColumn(width=280),
            "Interview score": st.column_config.NumberColumn(format="%d / 100", width="small"),
            "Report": st.column_config.LinkColumn(display_text="Open report ↗", width="small"),
        },
    )
    st.caption(f"Showing {(page-1)*8+1}–{min(page*8,len(filtered))} of {len(filtered)}")
except (ValueError, sqlite3.Error):
    st.error("The candidate list could not be loaded. Please refresh and try again.")
