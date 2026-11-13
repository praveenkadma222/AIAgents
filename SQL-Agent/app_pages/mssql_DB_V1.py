import streamlit as st
import pandas as pd
import re

from sqlalchemy import inspect, text
from langchain_ollama import OllamaLLM

from main import get_engine


st.title("🤖 AI SQL Analyst")

# Database connection
try:
    engine = get_engine()

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    st.success("SQL Server connected successfully!")

except Exception as e:
    st.error(f"Connection failed: {e}")
    st.stop()


# Get database schema internally
def get_schema():
    inspector = inspect(engine)
    schema = ""

    for table in inspector.get_table_names(schema="dbo"):
        columns = inspector.get_columns(table, schema="dbo")
        names = ", ".join(c["name"] for c in columns)
        schema += f"Table: dbo.{table}, Columns: {names}\n"

    return schema


# Generate SQL
def generate_sql(question):
    model = OllamaLLM(
        model="deepseek-r1:8b",
        base_url="http://localhost:11434",
        temperature=0
    )

    prompt = f"""
    Generate only a SQL Server SELECT query.
    Use only the provided database schema.
    Do not generate INSERT, UPDATE, DELETE or DROP.

    Schema:
    {get_schema()}

    Question:
    {question}
    """

    result = model.invoke(prompt)
    result = re.sub(r"<think>.*?</think>", "", result, flags=re.S)
    result = re.sub(r"```sql|```", "", result, flags=re.I)

    return result.strip().rstrip(";")


# Question input - this should always be visible
question = st.text_area(
    "Ask your database question",
    placeholder="Example: Show top 10 customers",
    height=100
)

if st.button("Analyze", type="primary"):

    if not question.strip():
        st.warning("Please enter your question.")

    else:
        try:
            with st.spinner("Generating SQL and fetching results..."):

                sql_query = generate_sql(question)

                if not re.match(r"^\s*SELECT\b", sql_query, re.I):
                    raise ValueError("Only SELECT queries are allowed.")

                if ";" in sql_query or "--" in sql_query:
                    raise ValueError("Invalid SQL query.")

                with engine.connect() as conn:
                    df = pd.read_sql_query(text(sql_query), conn)

            st.subheader("Query Results")

            if df.empty:
                st.info("No records found.")
            else:
                st.dataframe(df, use_container_width=True)
                st.caption(f"Total records: {len(df)}")

        except Exception as e:
            st.error(f"Error: {e}")