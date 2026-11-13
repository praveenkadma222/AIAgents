"""Streamlit interface for the CrewAI email drafting agent."""

import os

import streamlit as st
from crewai import Agent, Crew, Process, Task
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

DEFAULT_CONTEXT = (
    "Follow up on our product demo from last Tuesday. They seemed interested "
    "but haven't responded."
)


def build_email_crew(context: str, tone: str, recipient: str) -> str:
    """Analyze the request and draft an email with a two-agent CrewAI crew."""
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)

    analyst = Agent(
        role="Email Context Analyst",
        goal="Understand the email context, extract key points, and define the structure",
        backstory=(
            "You are an expert business communication analyst who distills "
            "complex situations into clear email requirements."
        ),
        llm=llm,
        verbose=False,
    )

    writer = Agent(
        role="Professional Email Writer",
        goal="Draft clear, concise, and effective professional emails",
        backstory=(
            "You are a professional copywriter specializing in business emails "
            "that get responses."
        ),
        llm=llm,
        verbose=False,
    )

    analyze_task = Task(
        description=f"""Analyze this email requirement:
Context: {context}
Recipient: {recipient}
Desired tone: {tone}

Extract: purpose, key points to cover, call to action, subject line suggestion.""",
        agent=analyst,
        expected_output=(
            "Structured email brief: purpose, key points, CTA, and suggested subject line"
        ),
    )

    write_task = Task(
        description=f"""Using the analysis, draft a complete professional email.
Tone: {tone}. Recipient: {recipient}.
Include: Subject line, greeting, body paragraphs, closing, signature placeholder.
Keep it concise — under 200 words for the body.""",
        agent=writer,
        expected_output="Complete formatted email ready to send",
        context=[analyze_task],
    )

    crew = Crew(
        agents=[analyst, writer],
        tasks=[analyze_task, write_task],
        process=Process.sequential,
        verbose=False,
    )
    return str(crew.kickoff())


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
    elif not os.getenv("OPENAI_API_KEY"):
        st.error(
            "OpenAI API key not found. Set OPENAI_API_KEY in your environment "
            "or in a local .env file, then try again."
        )
    else:
        with st.spinner("Analyzing the context and drafting your email..."):
            st.session_state["drafted_email"] = build_email_crew(
                context.strip(),
                tone.strip(),
                recipient.strip(),
            )

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
