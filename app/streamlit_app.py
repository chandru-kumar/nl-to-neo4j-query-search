"""
Streamlit Chat UI for the PA-Web Chatbot.

A conversational, assistant-style interface to query the Neo4j graph via
natural language — inspired by the Streamlit demo AI assistant: a welcome
screen with suggested prompts, streamed answers, and collapsible details
(generated Cypher + result table).
"""

import time

import requests
import pandas as pd
import streamlit as st

DEFAULT_API_URL = "http://localhost:8000"

st.set_page_config(
    page_title="PA-Web Assistant",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ── Styling ─────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
      .block-container { max-width: 820px; padding-top: 2.2rem; }
      .hero { text-align: center; margin: 1rem 0 1.6rem; }
      .hero h1 { font-size: 2.1rem; margin-bottom: .25rem; }
      .hero p { color: #6b7280; font-size: 1.02rem; margin: 0; }
      .suggest-label { color:#6b7280; font-size:.85rem; text-transform:uppercase;
                       letter-spacing:.06em; margin:.5rem 0 .4rem; }
      div[data-testid="stChatInput"] textarea { font-size: 1rem; }
      .stButton button { width: 100%; text-align: left; border-radius: 12px;
                         padding: .7rem .9rem; line-height: 1.25; }
      .status-dot { height:.6rem; width:.6rem; border-radius:50%;
                    display:inline-block; margin-right:.4rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Session state ───────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "api_url" not in st.session_state:
    st.session_state.api_url = DEFAULT_API_URL
if "pending" not in st.session_state:
    st.session_state.pending = None  # a question queued from a suggestion card


def api_health(url: str) -> bool:
    try:
        return requests.get(f"{url}/health", timeout=3).status_code == 200
    except requests.RequestException:
        return False


# ── Sidebar ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("⚙️ Settings")
    st.session_state.api_url = st.text_input("API URL", value=st.session_state.api_url)
    api_url = st.session_state.api_url

    online = api_health(api_url)
    dot = "#22c55e" if online else "#ef4444"
    st.markdown(
        f"<span class='status-dot' style='background:{dot}'></span>"
        f"{'Backend online' if online else 'Backend offline'}",
        unsafe_allow_html=True,
    )

    st.divider()
    st.subheader("🗄️ Data")
    if st.button("Load data into Neo4j"):
        with st.spinner("Loading data — this can take a while…"):
            try:
                resp = requests.post(f"{api_url}/load-data", timeout=1800)
                if resp.status_code == 200:
                    st.success("Data loaded successfully!")
                else:
                    st.error(resp.json().get("detail", "Unknown error"))
            except requests.RequestException as e:
                st.error(f"Connection error: {e}")

    st.divider()
    if st.button("🧹 Clear chat"):
        st.session_state.messages = []
        st.rerun()

    st.caption("PA-Web Assistant · NL → Cypher → Neo4j")

# ── Suggested prompts ───────────────────────────────────────────────────
SUGGESTIONS = [
    ("📊", "How many projects are there?"),
    ("🧩", "List all node types"),
    ("🔧", "What attributes does a pin have?"),
    ("✏️", "Which nodes have user-modified values?"),
    ("🌳", "Show children of node BS_CY329-CAN"),
    ("🔌", "Which attributes use the Dropdown widget?"),
]


def render_assistant_details(cypher, data):
    """Collapsible Cypher + result table shared by history and live replies."""
    if cypher:
        with st.expander("🔎 Generated Cypher"):
            st.code(cypher, language="cypher")
    if data:
        with st.expander(f"📋 Results ({len(data)} rows)", expanded=len(data) <= 25):
            st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)


def stream_text(text: str):
    """Yield words with a tiny delay so answers feel like they're typing."""
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.012)


def ask_backend(question: str):
    """Render a user turn + the assistant's reply, and persist both."""
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("Thinking…"):
            try:
                resp = requests.post(
                    f"{st.session_state.api_url}/chat",
                    json={"question": question},
                    timeout=60,
                )
            except requests.ConnectionError:
                msg = "I can't reach the backend. Make sure the API server is running."
                st.error(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})
                return
            except requests.RequestException as e:
                msg = f"Request failed: {e}"
                st.error(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})
                return

        if resp.status_code != 200:
            msg = f"API error {resp.status_code}: {resp.text}"
            st.error(msg)
            st.session_state.messages.append({"role": "assistant", "content": msg})
            return

        result = resp.json()
        answer = result.get("answer", "")
        cypher = result.get("cypher")
        data = result.get("data", [])
        error = result.get("error")

        st.write_stream(stream_text(answer))
        if error:
            st.warning(error)
        render_assistant_details(cypher, data)

    st.session_state.messages.append({
        "role": "assistant", "content": answer,
        "cypher": cypher, "data": data, "error": error,
    })


# ── Main area ───────────────────────────────────────────────────────────
if not st.session_state.messages:
    st.markdown(
        "<div class='hero'><h1>🤖 PA-Web Assistant</h1>"
        "<p>Ask anything about your projects, nodes, attributes and assignments "
        "— in plain English.</p></div>",
        unsafe_allow_html=True,
    )
    st.markdown("<div class='suggest-label'>Try asking</div>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (icon, text) in enumerate(SUGGESTIONS):
        if cols[i % 2].button(f"{icon}  {text}", key=f"sug_{i}"):
            st.session_state.pending = text
            st.rerun()
else:
    for m in st.session_state.messages:
        avatar = "🤖" if m["role"] == "assistant" else None
        with st.chat_message(m["role"], avatar=avatar):
            st.markdown(m["content"])
            if m["role"] == "assistant":
                if m.get("error"):
                    st.warning(m["error"])
                render_assistant_details(m.get("cypher"), m.get("data"))

# ── Input handling ──────────────────────────────────────────────────────
typed = st.chat_input("Ask a question about your project data…")
question = typed or st.session_state.pop("pending", None)
if question:
    ask_backend(question)
    # Re-render so the welcome screen collapses and the thread renders
    # consistently from history.
    st.rerun()
