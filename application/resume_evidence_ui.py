"""Compact resume evidence shared by application and shortlist views."""

import streamlit as st


def _brief(text, limit=160):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3].rsplit(" ", 1)[0] + "..."


def show_resume_evidence(assessment):
    st.markdown("**Match summary**")
    summaries = list(dict.fromkeys(
        component["explanation"] for component in assessment["components"].values()
        if component["applicable"]
    ))
    for summary in summaries:
        st.text("• " + _brief(summary))

    gaps = assessment["gaps"]
    if gaps:
        st.markdown("**Not demonstrated**")
        for text in list(dict.fromkeys(gap["explanation"] for gap in gaps))[:2]:
            st.text("• " + _brief(text))
        if len(gaps) > 2:
            st.caption("Additional gaps are included in the supporting passages.")

    with st.expander("Supporting passages", icon=":material/description:"):
        seen = set()
        for component in assessment["components"].values():
            if not component["applicable"]:
                continue
            for evidence in component["evidence"]:
                pair = (evidence["resume_quote"], evidence["jd_quote"])
                normalized = tuple(" ".join(text.split()) for text in pair)
                if normalized in seen:
                    continue
                seen.add(normalized)
                with st.container(border=True):
                    resume, job = st.columns(2)
                    resume.caption("Resume")
                    resume.text(pair[0])
                    job.caption("Job requirement")
                    job.text(pair[1])
        for gap in gaps:
            st.caption("Not demonstrated in the resume")
            st.text(gap["explanation"])
            st.text(gap["jd_quote"])
