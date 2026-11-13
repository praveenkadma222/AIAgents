
import logging
import os

import streamlit as st
import pandas as pd

from sqlalchemy import (
    MetaData,
    Table,
    create_engine,
    inspect,
    select,
    text
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError


# --------------------------------------------------
# Application Configuration
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="Supabase SQL Agent",
    page_icon="🗄️",
    layout="wide"
)

st.title("🗄️ Supabase SQL Agent")
st.caption("Connect and browse your Supabase PostgreSQL database")


# --------------------------------------------------
# Get Database URL
# --------------------------------------------------

def get_database_url() -> str | None:

    # First: Environment variable
    database_url = os.getenv("DATABASE_URL", "").strip()

    if database_url:
        return database_url

    # Second: Streamlit secrets
    try:
        database_url = st.secrets.get("DATABASE_URL", "")
    except Exception:
        return None

    return database_url.strip() or None


# --------------------------------------------------
# Create SQLAlchemy Engine
# --------------------------------------------------

@st.cache_resource(max_entries=2)
def get_engine(database_url: str) -> Engine:

    url = make_url(database_url)

    if url.get_backend_name() != "postgresql":
        raise ValueError(
            "Invalid DATABASE_URL. PostgreSQL connection required."
        )

    # Configure Psycopg 3
    url = url.set(
        drivername="postgresql+psycopg"
    )

    # Require SSL
    if "sslmode" not in url.query:
        url = url.update_query_dict(
            {"sslmode": "require"}
        )

    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": 15
        }
    )

    return engine


# --------------------------------------------------
# Get Database Connection
# --------------------------------------------------

database_url = get_database_url()

if not database_url:

    st.error(
        "DATABASE_URL not found. "
        "Configure it in .streamlit/secrets.toml"
    )

    st.info(
        "Current working directory: "
        + os.getcwd()
    )

    st.stop()


# --------------------------------------------------
# Connect to Supabase
# --------------------------------------------------

try:

    engine = get_engine(database_url)

    with engine.connect() as connection:

        database_info = connection.execute(
            text(
                """
                SELECT
                    current_database() AS database_name,
                    current_user AS database_user,
                    version() AS postgres_version
                """
            )
        ).mappings().one()

        table_names = inspect(
            connection
        ).get_table_names(
            schema="public"
        )

    st.success("Connected to Supabase successfully!")

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "Database",
        database_info["database_name"]
    )

    col2.metric(
        "Database User",
        database_info["database_user"]
    )

    col3.metric(
        "Public Tables",
        len(table_names)
    )

except (SQLAlchemyError, ValueError) as error:

    logger.exception(
        "Supabase database connection failed"
    )

    st.error(
        f"Could not connect to Supabase: {error}"
    )

    st.stop()


# --------------------------------------------------
# Table Browser
# --------------------------------------------------

st.divider()

st.subheader("📋 Database Tables")

if not table_names:

    st.info(
        "No tables found in the public schema."
    )

    st.stop()


with st.form("table_browser_form"):

    col1, col2, col3 = st.columns(3)

    with col1:

        table_name = st.selectbox(
            "Select Table",
            table_names
        )

    with col2:

        page_number = st.number_input(
            "Page Number",
            min_value=1,
            value=1,
            step=1
        )

    with col3:

        page_size = st.selectbox(
            "Rows Per Page",
            [25, 50, 100, 250],
            index=1
        )

    submitted = st.form_submit_button(
        "Load Table Data",
        use_container_width=True
    )


# --------------------------------------------------
# Load Table Data
# --------------------------------------------------

if submitted:

    try:

        with engine.connect() as connection:

            with connection.begin():

                # Enforce read-only transaction
                connection.exec_driver_sql(
                    "SET TRANSACTION READ ONLY"
                )

                # Load table metadata
                table = Table(
                    table_name,
                    MetaData(),
                    schema="public",
                    autoload_with=connection
                )

                # Build SQL query safely
                query = (
                    select(table)
                    .limit(page_size)
                    .offset(
                        (page_number - 1) * page_size
                    )
                )

                result = connection.execute(
                    query
                )

                rows = [
                    dict(row._mapping)
                    for row in result
                ]

        if rows:

            dataframe = pd.DataFrame(rows)

            st.subheader(
                f"Table: public.{table_name}"
            )

            st.dataframe(
                dataframe,
                use_container_width=True,
                hide_index=True
            )

            st.success(
                f"Successfully loaded {len(rows)} records."
            )

            st.caption(
                f"Page {page_number} | "
                f"Page size: {page_size}"
            )

        else:

            st.info(
                "No records found on this page."
            )

    except SQLAlchemyError as error:

        logger.exception(
            "Failed to load table public.%s",
            table_name
        )

        st.error(
            f"Failed to load table data: {error}"
        )


# --------------------------------------------------
# Database Schema Information
# --------------------------------------------------

st.divider()

st.subheader("🔍 Database Schema")

selected_schema_table = st.selectbox(
    "Select table to view columns",
    table_names,
    key="schema_table"
)

if st.button("View Table Schema"):

    try:

        inspector = inspect(engine)

        columns = inspector.get_columns(
            selected_schema_table,
            schema="public"
        )

        schema_data = []

        for column in columns:

            schema_data.append({
                "Column Name": column["name"],
                "Data Type": str(column["type"]),
                "Nullable": column["nullable"],
                "Primary Key": column.get(
                    "primary_key",
                    False
                )
            })

        st.dataframe(
            pd.DataFrame(schema_data),
            use_container_width=True,
            hide_index=True
        )

    except SQLAlchemyError as error:

        st.error(
            f"Could not load schema: {error}"
        )


# --------------------------------------------------
# Footer
# --------------------------------------------------

st.divider()

st.caption(
    "Powered by Streamlit + SQLAlchemy + Supabase PostgreSQL"
)

