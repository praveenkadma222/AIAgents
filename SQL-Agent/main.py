
# main.py

import os
import re
import json
import logging

import streamlit as st
import pandas as pd

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import OllamaLLM


# --------------------------------------------------
# Configuration
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OLLAMA_MODEL = "deepseek-r1:8b"
OLLAMA_BASE_URL = "http://localhost:11434"


# --------------------------------------------------
# Step 1: Get Supabase Database URL
# --------------------------------------------------

def get_database_url():

    # Check environment variable first
    database_url = os.getenv("DATABASE_URL", "").strip()

    if database_url:
        return database_url

    # Check Streamlit secrets
    try:
        return st.secrets.get("DATABASE_URL", "").strip() or None
    except Exception:
        return None


# --------------------------------------------------
# Step 2: Create Supabase Connection
# --------------------------------------------------

@st.cache_resource
def get_engine(database_url: str) -> Engine:

    url = make_url(database_url)

    if url.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL must use PostgreSQL.")

    # Configure Psycopg 3
    url = url.set(drivername="postgresql+psycopg")

    # Supabase SSL configuration
    if "sslmode" not in url.query:
        url = url.update_query_dict(
            {"sslmode": "require"}
        )

    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": 15
        }
    )

    return engine


# --------------------------------------------------
# Step 3: Extract Supabase Database Schema
# --------------------------------------------------

def extract_schema():

    database_url = get_database_url()

    if not database_url:
        raise ValueError(
            "DATABASE_URL not found in Streamlit secrets "
            "or environment variables."
        )

    engine = get_engine(database_url)

    inspector = inspect(engine)

    schema = {}

    # Read tables from public schema
    table_names = inspector.get_table_names(
        schema="public"
    )

    for table_name in table_names:

        columns = inspector.get_columns(
            table_name,
            schema="public"
        )

        schema[f"public.{table_name}"] = [
            {
                "column": col["name"],
                "type": str(col["type"]),
                "nullable": col["nullable"]
            }
            for col in columns
        ]

    return json.dumps(
        schema,
        indent=4
    )


# --------------------------------------------------
# Step 4: Natural Language to SQL
# --------------------------------------------------

def text_to_sql(schema, prompt):

    SYSTEM_PROMPT = """
    You are an expert PostgreSQL SQL developer.

    Your task is to convert natural language questions into
    valid PostgreSQL SELECT queries.

    Database rules:

    1. Use only tables and columns present in the schema.
    2. All tables belong to the public schema.
    3. Use PostgreSQL-compatible syntax.
    4. Use correct JOIN conditions.
    5. Use meaningful column aliases.
    6. Use LIMIT when the user requests limited records.
    7. Do not invent table names or column names.
    8. Generate only SELECT queries.
    9. Do not generate INSERT, UPDATE, DELETE, DROP,
       ALTER, CREATE, TRUNCATE or other modification queries.
    10. Never use multiple SQL statements.
    11. Do not generate explanations or Markdown.

    Return ONLY the SQL query.
    """

    prompt_template = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        (
            "human",
            """
            Database Schema:
            {schema}

            Natural Language Question:
            {user_prompt}

            Generate the PostgreSQL SELECT query:
            """
        )
    ])

    model = OllamaLLM(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0
    )

    chain = prompt_template | model

    raw = chain.invoke({
        "schema": schema,
        "user_prompt": prompt
    })

    # Remove DeepSeek thinking section
    cleaned_text = re.sub(
        r"<think>.*?</think>",
        "",
        raw,
        flags=re.DOTALL
    )

    # Remove Markdown SQL blocks
    cleaned_text = re.sub(
        r"```(?:sql)?",
        "",
        cleaned_text,
        flags=re.IGNORECASE
    )

    cleaned_text = cleaned_text.replace("```", "").strip()

    # Extract the SQL statement
    match = re.search(
        r"\b(?:SELECT|WITH)\b.*",
        cleaned_text,
        flags=re.IGNORECASE | re.DOTALL
    )

    if not match:
        raise ValueError(
            "AI did not generate a valid SQL query."
        )

    sql_query = match.group(0).strip()

    return sql_query


# --------------------------------------------------
# Step 5: Validate Generated SQL
# --------------------------------------------------

def validate_sql(sql_query):

    query = sql_query.strip()

    # Allow only SELECT or WITH statements
    if not re.match(
        r"^(SELECT|WITH)\b",
        query,
        flags=re.IGNORECASE
    ):
        raise ValueError(
            "Only SELECT queries are allowed."
        )

    # Allow one optional trailing semicolon
    without_trailing_semicolon = query.rstrip().rstrip(";").strip()

    if ";" in without_trailing_semicolon:
        raise ValueError(
            "Multiple SQL statements are not allowed."
        )

    # Reject SQL comments
    if "--" in query or "/*" in query or "*/" in query:
        raise ValueError(
            "SQL comments are not allowed."
        )

    return query


# --------------------------------------------------
# Step 6: Execute SQL Against Supabase
# --------------------------------------------------

def execute_sql(sql_query):

    database_url = get_database_url()

    if not database_url:
        raise ValueError("DATABASE_URL not configured.")

    engine = get_engine(database_url)

    # Validate generated SQL
    sql_query = validate_sql(sql_query)

    try:

        with engine.connect() as connection:

            with connection.begin():

                # Enforce read-only transaction
                connection.exec_driver_sql(
                    "SET TRANSACTION READ ONLY"
                )

                result = connection.execute(
                    text(sql_query)
                )

                columns = list(result.keys())

                rows = [
                    dict(row._mapping)
                    for row in result.fetchall()
                ]

        return {
            "columns": columns,
            "rows": rows,
            "dataframe": pd.DataFrame(
                rows,
                columns=columns
            )
        }

    except SQLAlchemyError as error:

        logger.exception("SQL execution failed.")

        raise RuntimeError(
            f"Database query failed: {error}"
        ) from error


# --------------------------------------------------
# Step 7: Main AI Data Analyst Function
# --------------------------------------------------

def get_data_from_database(user_query):

    if not user_query or not user_query.strip():
        raise ValueError(
            "Please enter a question."
        )

    # Step 1: Extract Supabase schema
    schema = extract_schema()

    # Step 2: Convert natural language into SQL
    sql_query = text_to_sql(
        schema,
        user_query
    )

    logger.info(
        "Generated SQL: %s",
        sql_query
    )

    # Step 3: Validate and execute SQL
    database_result = execute_sql(
        sql_query
    )

    # Step 4: Return results
    return {
        "question": user_query,
        "sql_query": sql_query,
        "columns": database_result["columns"],
        "rows": database_result["rows"],
        "dataframe": database_result["dataframe"],
        "total_records": len(
            database_result["rows"]
        )
    }
