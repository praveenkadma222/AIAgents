
import streamlit as st

from main import get_data_from_database

st.set_page_config(
    page_title="AI Data Analyst",
    page_icon="📊",
    layout="centered"
)

st.title("📊 AI Data Analyst")

st.markdown(
    "Ask questions about your database in natural language. "
    "AI will generate SQL, execute it, and show the results."
)

user_query = st.text_area(
    "Ask your question:",
    placeholder="e.g., Show the top 10 customers by revenue",
    height=120
)

if st.button("Analyze", type="primary", use_container_width=True):

    if not user_query.strip():

        st.warning("Please enter a question to analyze.")

    else:

        with st.spinner("Understanding your question and analyzing data..."):

            try:
                database_response = get_data_from_database(user_query)

                st.success("Analysis complete!")

                st.subheader("Analysis Results")

                if isinstance(database_response, str):
                    st.markdown(database_response)
                elif database_response is not None:
                    st.write(database_response)
                else:
                    st.info("No results found.")

            except Exception as error:
                st.error("Unable to analyze your question.")
                st.exception(error)
