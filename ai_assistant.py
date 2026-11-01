
import streamlit as st
from google import genai


def get_gemini_response(prompt):
    try:
        api_key = st.secrets["GEMINI_API_KEY"]

        client = genai.Client(api_key=api_key)

        response = client.interactions.create(
            model="gemini-3.8-flash",
            input=prompt
        )

        return response.output_text

    except Exception as error:
        return f"AI Error: {error}"
