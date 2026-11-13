# SQL Agent

## Run the Streamlit app

Replace the empty `DATABASE_URL` value in `.streamlit/secrets.toml` with your
rotated Supabase connection URL:

```toml
DATABASE_URL = "postgresql://..."
```

Alternatively, set the `DATABASE_URL` environment variable. Do not commit the
connection URL or passwords.

Run the app with:

```powershell
uv run streamlit run frontend.py
```

The **Supabase data** page lists tables in the `public` schema and displays
selected rows in pages. Reads are issued in read-only transactions. Use a
dedicated database role with only the required `SELECT` privileges; the
read-only page setting is not a replacement for least-privilege credentials.

SQLAgent/
│
├── frontend.py
├── main.py
│
├── app_pages/
│   ├── ai_analyst.py
│   ├── supabase_data.py
│   └── mssql_data.py
│
├── .streamlit/
│   └── secrets.toml
│
└── requirements.txt