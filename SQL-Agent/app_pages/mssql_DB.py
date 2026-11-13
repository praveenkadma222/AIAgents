
import streamlit as st
import pandas as pd
import pyodbc
import re

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from langchain_ollama import OllamaLLM


# SQL Server connection
@st.cache_resource
def get_mssql_engine():

    config = st.secrets["MSSQL"]

    driver = "ODBC Driver 17 for SQL Server"

    if driver not in pyodbc.drivers():
        raise Exception(f"{driver} is not installed.")

    url = URL.create(
        "mssql+pyodbc",
        username=config["username"],
        password=config["password"],
        host=config["server"],
        database=config["database"],
        query={
            "driver": driver,
            "Encrypt": "yes",
            "TrustServerCertificate": "yes"
        }
    )

    return create_engine(url, pool_pre_ping=True)


# Connect to SQL Server
try:
    engine = get_mssql_engine()

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    st.title("AI SQL Analyst")
    st.success("SQL Server connected successfully!")

except Exception as e:
    st.error(f"Connection failed: {e}")
    st.stop()


# Read database schema internally
def get_schema():

    inspector = inspect(engine)
    schema = ""

    for table in inspector.get_table_names(schema="dbo"):

        columns = inspector.get_columns(table, schema="dbo")

        column_names = ", ".join(
            column["name"] for column in columns
        )

        schema += f"Table: dbo.{table}, Columns: {column_names}\n"

    return schema


# Generate SQL from natural language
def generate_sql(question):

    model = OllamaLLM(
        model="deepseek-r1:8b",
        base_url="http://localhost:11434",
        temperature=0
    )

    prompt = f"""
    You are a Microsoft SQL Server expert.

    Convert the question into one valid SQL Server SELECT query.

    Rules:
    - Use only tables and columns from the schema.
    - Use SQL Server syntax.
    - Generate SELECT queries only.
    - Never generate INSERT, UPDATE, DELETE or DROP.
    - Return SQL only.

    Database schema:
    {get_schema()}

    User question:
    {question}
    """

    result = model.invoke(prompt)

    result = re.sub(
        r"<think>.*?</think>", "", result, flags=re.S
    )

    result = re.sub(
        r"```sql|```", "", result, flags=re.I
    )

    return result.strip().rstrip(";")


# User input
question = st.text_area(
    "Ask your database question",
    placeholder="Example: Show the top 10 customers",
    height=100
)

if st.button("Analyze", type="primary"):

    if not question.strip():
        st.warning("Please enter your question.")

    else:
        try:
            with st.spinner("Analyzing and fetching data..."):

                sql_query = generate_sql(question)

                # Basic SELECT-only check
                if not re.match(r"^\s*SELECT\b", sql_query, re.I):
                    raise ValueError("Only SELECT queries are allowed.")

                if ";" in sql_query or "--" in sql_query:
                    raise ValueError("Invalid SQL query.")

                with engine.connect() as conn:
                    df = pd.read_sql_query(
                        text(sql_query), conn
                    )

            st.subheader("Query Results")

            if df.empty:
                st.info("No records found.")
            else:
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True
                )

                st.caption(f"Total records: {len(df)}")

        except Exception as e:
            st.error(f"Error: {e}")
