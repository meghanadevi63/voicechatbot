import os

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8001")

st.set_page_config(page_title="RAG Chatbot", page_icon="💬")
st.title("💬 RAG Chatbot")

if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("Settings")
    try:
        requests.get(f"{BACKEND_URL}/health", timeout=3).raise_for_status()
        st.success("Backend connected")
    except requests.RequestException:
        st.error(f"Backend not reachable at {BACKEND_URL}")

    if st.button("Re-ingest docs"):
        with st.spinner("Ingesting documents..."):
            try:
                r = requests.post(f"{BACKEND_URL}/ingest", timeout=1800)
                r.raise_for_status()
                info = r.json()
                st.success(f"Indexed {info['chunks']} chunks from {len(info['files'])} file(s)")
            except requests.RequestException as e:
                st.error(f"Ingest failed: {e}")

    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()


def show_sources(sources):
    if not sources:
        return
    with st.expander("Sources"):
        for s in sources:
            st.markdown(f"**{s['source']} — page {s['page']}**")
            st.caption(s["content"][:500] + ("..." if len(s["content"]) > 500 else ""))


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        show_sources(msg.get("sources"))

if question := st.chat_input("Ask a question about your documents"):
    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                r = requests.post(
                    f"{BACKEND_URL}/chat",
                    json={"question": question, "history": history},
                    timeout=120,
                )
                r.raise_for_status()
                data = r.json()
                answer, sources = data["answer"], data["sources"]
            except requests.RequestException as e:
                resp = getattr(e, "response", None)
                detail = resp.text if resp is not None else ""
                answer, sources = f"Error: {e} {detail}", []
        st.markdown(answer)
        show_sources(sources)

    st.session_state.messages.append({"role": "assistant", "content": answer, "sources": sources})
