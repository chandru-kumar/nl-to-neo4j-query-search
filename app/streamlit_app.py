"""
Streamlit Chat UI for PA-Web Chatbot.
Provides a conversational interface to query Neo4j via natural language.
"""

import streamlit as st
import requests
import pandas as pd

API_URL = "http://localhost:8000"

st.set_page_config(
    page_title="PA-Web Chatbot",
    page_icon="🤖",
    layout="wide",
)

st.title("PA-Web Chatbot")
st.caption("Ask questions about your project data in natural language")

# Sidebar
with st.sidebar:
    st.header("Settings")
    api_url = st.text_input("API URL", value=API_URL)

    st.divider()
    st.header("Actions")
    if st.button("Load Data into Neo4j", type="secondary"):
        with st.spinner("Loading data... This may take a while."):
            try:
                resp = requests.post(f"{api_url}/load-data", timeout=600)
                if resp.status_code == 200:
                    st.success("Data loaded successfully!")
                else:
                    st.error(f"Error: {resp.json().get('detail', 'Unknown error')}")
            except Exception as e:
                st.error(f"Connection error: {e}")

    st.divider()
    st.header("Example Questions")
    examples = [
        "Show all projects",
        "List all node types",
        "Show nodes of type functionModule",
        "What attributes does a pin have?",
        "Which nodes have user-modified values?",
        "Show children of node BS_CY329-CAN",
        "How many nodes are in project FCOMP?",
    ]
    for ex in examples:
        if st.button(ex, key=f"ex_{ex}"):
            st.session_state["prefill_question"] = ex

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if "cypher" in message and message["cypher"]:
            with st.expander("Generated Cypher Query"):
                st.code(message["cypher"], language="cypher")
        if "data" in message and message["data"]:
            with st.expander(f"Results ({len(message['data'])} rows)"):
                df = pd.DataFrame(message["data"])
                st.dataframe(df, use_container_width=True)

# Handle prefilled question from sidebar
prefill = st.session_state.pop("prefill_question", None)

# Chat input
prompt = st.chat_input("Ask a question about your project data...") or prefill

if prompt:
    # Add user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Get response from API
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                resp = requests.post(
                    f"{api_url}/chat",
                    json={"question": prompt},
                    timeout=30,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    answer = result["answer"]
                    cypher = result.get("cypher")
                    data = result.get("data", [])
                    error = result.get("error")

                    st.markdown(answer)

                    if error:
                        st.error(f"Error: {error}")

                    if cypher:
                        with st.expander("Generated Cypher Query"):
                            st.code(cypher, language="cypher")

                    if data:
                        with st.expander(f"Results ({len(data)} rows)", expanded=True):
                            df = pd.DataFrame(data)
                            st.dataframe(df, use_container_width=True)

                    # Save to history
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": answer,
                        "cypher": cypher,
                        "data": data,
                    })
                else:
                    err_msg = f"API Error: {resp.status_code} - {resp.text}"
                    st.error(err_msg)
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": err_msg,
                    })

            except requests.ConnectionError:
                err_msg = "Cannot connect to the API. Make sure the backend is running."
                st.error(err_msg)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": err_msg,
                })
            except Exception as e:
                err_msg = f"Unexpected error: {e}"
                st.error(err_msg)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": err_msg,
                })
