import logging
from typing import Literal

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.vectorstore import get_vectorstore

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """You are the intent router for a voice assistant that answers questions about a company's documents.
Classify the user's latest message into exactly one intent:

- "small_talk": the message is ONLY social conversation: greetings (hi, good morning, how are you),
  thanks, congratulations or wishes (happy birthday, well done), compliments, acknowledgements
  (ok, got it), or goodbyes.
- "document_query": anything else. This includes any question or request for information, follow-ups
  such as "tell me more" or "what about the second one?", and messages that mix small talk with a
  question (e.g. "thanks! and what is the leave policy?").

When unsure, choose "document_query".

If the intent is "small_talk", write a warm, natural reply of one or two short sentences in `reply`.
Do not state any facts or answer questions. You may offer to help with questions about the documents.

If the intent is "document_query", write a standalone search query in `search_query`: rewrite the
message using the conversation history so it makes sense on its own (resolve "it", "that", "the second
one", etc.) and keep the key terms. Leave `reply` empty."""

SYSTEM_PROMPT = """You are a friendly voice assistant that answers questions using the document excerpts provided below.

Rules:
- Answer only from the excerpts. Do not use outside knowledge or guess.
- If the excerpts do not contain the answer, say so plainly, for example: "I couldn't find that in the documents."
  If they contain part of the answer, give that part and say what is missing.
- Use the conversation history to understand follow-up questions.
- Your reply will be read aloud, so write in natural spoken sentences: no markdown, bullet symbols, tables,
  headings or URLs. When listing a few items, say them in a sentence ("first..., then...").
- Keep it short: two to four sentences, unless the user asks for more detail.
- Don't mention "excerpts", "context" or chunk labels. If it helps, name the source document naturally
  (e.g. "According to the HR policy...").

Document excerpts:
{context}"""

MAX_HISTORY_MESSAGES = 6


class Route(BaseModel):
    """Routing decision for the user's latest message."""

    intent: Literal["small_talk", "document_query"] = Field(
        description="small_talk for purely social messages, document_query for everything else"
    )
    reply: str = Field(default="", description="Short reply, only when intent is small_talk")
    search_query: str = Field(
        default="", description="Standalone search query, only when intent is document_query"
    )


def format_docs(docs: list[Document]) -> str:
    return "\n\n".join(
        f"[{d.metadata.get('source')} p.{d.metadata.get('page')}]\n{d.page_content}"
        for d in docs
    )


def to_messages(history: list[dict]) -> list:
    messages = []
    for turn in history[-MAX_HISTORY_MESSAGES:]:
        cls = HumanMessage if turn["role"] == "user" else AIMessage
        messages.append(cls(content=turn["content"]))
    return messages


class RAGChain:
    def __init__(self):
        settings = get_settings()
        self.retriever = get_vectorstore().as_retriever(search_kwargs={"k": settings.top_k})

        router_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", ROUTER_PROMPT),
                MessagesPlaceholder("history"),
                ("human", "{question}"),
            ]
        )
        router_llm = ChatGroq(
            model=settings.router_model, api_key=settings.groq_api_key, temperature=0
        )
        self.router = router_prompt | router_llm.with_structured_output(Route)

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", SYSTEM_PROMPT),
                MessagesPlaceholder("history"),
                ("human", "{question}"),
            ]
        )
        llm = ChatGroq(model=settings.groq_model, api_key=settings.groq_api_key, temperature=0)
        self.chain = prompt | llm | StrOutputParser()

    def refresh(self):
        """Rebuild the retriever after re-ingestion (collection was dropped and recreated)."""
        self.retriever = get_vectorstore().as_retriever(
            search_kwargs={"k": get_settings().top_k}
        )

    def route(self, question: str, messages: list) -> Route:
        try:
            return self.router.invoke({"history": messages, "question": question})
        except Exception:
            # Never block an answer on the router: fall back to normal retrieval.
            logger.exception("Intent router failed; falling back to retrieval")
            return Route(intent="document_query", search_query=question)

    def answer(self, question: str, history: list[dict]) -> dict:
        messages = to_messages(history)
        route = self.route(question, messages)
        logger.info("Routed %r -> %s", question, route.intent)

        if route.intent == "small_talk" and route.reply.strip():
            return {"answer": route.reply.strip(), "sources": []}

        docs = self.retriever.invoke(route.search_query.strip() or question)
        answer = self.chain.invoke(
            {
                "context": format_docs(docs),
                "history": messages,
                "question": question,
            }
        )
        sources = [
            {
                "source": d.metadata.get("source"),
                "page": d.metadata.get("page"),
                "content": d.page_content,
            }
            for d in docs
        ]
        return {"answer": answer, "sources": sources}
