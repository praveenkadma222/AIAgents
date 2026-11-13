
import streamlit as st

st.set_page_config(
    page_title="AI Data Analyst",
    page_icon="🤖",
    layout="wide",
)

page = st.navigation(
    [
        st.Page(
            "app_pages/ai_analyst.py",
            title="AI analyst",
            icon=":material/auto_awesome:",
            default=True,
        ),
        st.Page(
            "app_pages/supabase_DB.py",
            title="Supabase data",
            icon=":material/table_view:",
        ),
        st.Page(
            "app_pages/mssql_DB.py",
            title="SQL Server data",
            icon=":material/storage:",
        ),
    ],
    position="top",
)

page.run()
