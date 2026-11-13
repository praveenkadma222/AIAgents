#step1: Extrac schema
import re
import sqlite3
import json

from sqlalchemy import create_engine, inspect
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import OllamaLLM

db_url = 'sqlite:///my_database.db'

def extract_schema(db_url):
    engine = create_engine(db_url)
    inspector = inspect(engine)

    schema = {}
    for table_name in inspector.get_table_names():
        columns = inspector.get_columns(table_name)
        schema[table_name] = [col['name'] for col in columns]
    return json.dumps(schema, indent=4)

# for table_name in inspector.get_table_names():
#     print(f"Table: {table_name}")
#     for field_info in inspector.get_columns(table_name):
#         print(f"Column: {field_info['name']}, Type: {field_info['type']}, Nullable: {field_info['nullable']}")

# for field_info in inspector.get_columns('customers'):
#     print(f"Column: {field_info['name']}, Type: {field_info['type']}, Nullable: {field_info['nullable']}")
#step2: Text to sql (deepseek with ollama)

def text_to_sql(schema, prompt):
        SYSTEM_PROMPT = """
        You are a SQL expert. You will be given a database schema and a natural language query. 
        You will generate a SQL query that retrieves the requested information from the database.
        ensure the sql query is compatible with the provided database schema and avoid using any unsupported features.
        No preamble please.
        """
        prompt_template = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", "Database Schema:\n{schema}\n\nNatural Language Query:\n{user_prompt}\n\nSQL Query:")

        ])

        model=OllamaLLM(model="deepseek-r1:8b", base_url="http://localhost:11434")

        chain = prompt_template | model
        raw = chain.invoke({"schema": schema, "user_prompt": prompt})
        cleaned_text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        return cleaned_text.strip()

def get_data_from_database(user_query):
    schema = extract_schema(db_url)
    sql_query = text_to_sql(schema, user_query)
    print("Generated SQL Query:", sql_query)

    # Execute the SQL query against the database
    conn = sqlite3.connect('my_database.db')
    cursor = conn.cursor()
    try:
        result = cursor.execute(sql_query)
        return result.fetchall()
    except Exception as e:
        return f"Error executing SQL query: {e}"
    finally:
        conn.close()