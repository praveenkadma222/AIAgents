"""Streamlit interface for the CrewAI email drafting agent."""

import os

import streamlit as st
from dotenv import load_dotenv
from groq import APIError, Groq

load_dotenv()

DEFAULT_CONTEXT = (
    "Follow up on our product demo from last Tuesday. They seemed interested "
    "but haven't responded."
)
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def build_email_draft(context: str, tone: str, recipient: str) -> str:
    """Analyze the request and draft an email using two Groq chat completions."""
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    analysis = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.3,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert business communication analyst. "
                    "Distill email requests into a clear brief."
                ),
            },
            {
                "role": "user",
                "content": f"""Analyze this email requirement:
Context: {context}
Recipient: {recipient}
Desired tone: {tone}

Extract the purpose, key points, call to action, and a suggested subject line.""",
            },
        ],
    )
    email_brief = analysis.choices[0].message.content
    if not email_brief:
        raise ValueError("Groq returned an empty email analysis.")

    draft = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.3,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a professional copywriter specializing in clear, "
                    "concise, effective business emails."
                ),
            },
            {
                "role": "user",
                "content": f"""Using this analysis, draft a complete professional email:
{email_brief}

Tone: {tone}. Recipient: {recipient}.
Include: Subject line, greeting, body paragraphs, closing, signature placeholder.
Keep the body under 200 words.""",
            },
        ],
    )
    email = draft.choices[0].message.content
    if not email:
        raise ValueError("Groq returned an empty email draft.")
    return email


st.set_page_config(
    page_title="Email drafting agent",
    page_icon=":material/mail:",
    layout="centered",
)

st.title("Email drafting agent")
st.caption("Turn your context and tone requirements into a polished email draft.")

with st.container(border=True):
    st.subheader("Email details")
    with st.form("email_draft_form"):
        context = st.text_area(
            "What is the email about?",
            value=DEFAULT_CONTEXT,
            height=130,
        )
        recipient_column, tone_column = st.columns(2)
        with recipient_column:
            recipient = st.text_input("Recipient", value="a potential client")
        with tone_column:
            tone = st.text_input("Desired tone", value="professional and friendly")
        submitted = st.form_submit_button(
            "Draft email",
            type="primary",
            icon=":material/auto_awesome:",
            width="stretch",
        )

if submitted:
    if not context.strip():
        st.error("Enter some context for the email before drafting.")
    elif not recipient.strip():
        st.error("Enter who the email is for.")
    elif not tone.strip():
        st.error("Enter the tone you want the email to use.")
    elif not os.getenv("GROQ_API_KEY"):
        st.error(
            "Groq API key not found. Set GROQ_API_KEY in your environment "
            "or in a local .env file, then try again."
        )
    else:
        try:
            with st.spinner("Analyzing the context and drafting your email..."):
                st.session_state["drafted_email"] = build_email_draft(
                    context.strip(),
                    tone.strip(),
                    recipient.strip(),
                )
        except APIError as exc:
            if "model_not_found" in str(exc):
                st.error(
                    f"Groq model {GROQ_MODEL!r} is unavailable for this API key. "
                    "Choose an active model from https://console.groq.com/docs/models "
                    "and set GROQ_MODEL to its model ID in your environment or .env file."
                )
            else:
                st.error(f"Groq request failed: {exc}")
        except ValueError as exc:
            st.error(str(exc))

if "drafted_email" in st.session_state:
    st.divider()
    st.subheader("Drafted email")
    st.code(st.session_state["drafted_email"], language=None)
    st.download_button(
        "Download draft",
        data=st.session_state["drafted_email"],
        file_name="email-draft.txt",
        mime="text/plain",
        icon=":material/download:",
    )
