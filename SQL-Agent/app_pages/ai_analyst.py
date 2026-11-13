import streamlit as st

from main import get_data_from_database

st.title("AI Data Analyst")
st.markdown("Ask questions about your data in natural language.")

user_query = st.text_area(
    "Enter your question:",
    placeholder="e.g., Total products sold in 2025",
)

if st.button("Analyze"):
    if not user_query.strip():
        st.warning("Please enter a question to analyze.")
    else:
        with st.spinner("Analyzing your query..."):
            database_response = get_data_from_database(user_query)

        st.success("Analysis complete!")
        st.markdown(f"Here's the analysis for your query:\n\n**{database_response}**")
