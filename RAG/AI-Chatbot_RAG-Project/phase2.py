#phase 1 import
import logging
import warnings
import os

import streamlit as st

#phase2
from langchain_groq import ChatGroq
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

#disable warnings and info logs
warnings.filterwarnings("ignore")
logging.getLogger("langchain").setLevel(logging.ERROR)

#phase 2 import
st.title ("RAG Chatbot")
# setup a session state variable to hold all the old messages
if 'messages' not in st.session_state:
    st.session_state.messages = []
 # disply all the chat messages from history on app rerun
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])   

prompt = st.chat_input("Ask me anything !")

if prompt:
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})
    #LLm setup
    groq_sys_prompt = chat_prompt = ChatPromptTemplate.from_template("" \
    """You are smart at everyting , you always give the best, the most accurate and most precise answers. answer the floowing question: {user_prompt}, start the anser directly.no small talk please.""")

    model="llama3-8b-8192"
    groq_cha = ChatGroq(
        api_key=os.environ.get("GROQ_API_KEY"),
        model_name=model
    )
    chain = groq_sys_prompt | groq_cha | StrOutputParser()
    response = chain.invoke({"user_prompt": prompt})

    #response getting displayed in the chat message container
    st.chat_message("assistant").markdown(response)
    st.session_state.messages.append({"role": "assistant", "content": response})