
import streamlit as st
from ai_assistant import get_gemini_response

st.title("CampusShare AI – Gemini Connection Test")

if st.button("Test Gemini Connection"):
    with st.spinner("Connecting to Gemini..."):
        response = get_gemini_response(
            "Introduce yourself in one sentence as an AI assistant for a campus resource-sharing platform."
        )

    st.write("### Gemini Response")
    st.write(response)
