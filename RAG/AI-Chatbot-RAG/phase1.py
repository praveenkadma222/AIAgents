import streamlit as st
st.title ("RAG Chatbot")

#phase2
import os
from langchain_groq import ChatGroq
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate


#setup a session state variable to hold all the old messages
if "messages" not in st.session_state:
    st.session_state.messages = []

#Display the c
# hat messages from history on app rerun

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])    

prompt = st.chat_input("Ask me anything about the RAG Chatbot!")
if prompt:
    st.chat_message("user").write(prompt)
    with st.chat_message("assistant"):
        st.info("This is a RAG Chatbot that can answer questions about the RAG Chatbot.")
        st.info("Please ask me anything about the RAG Chatbot!")