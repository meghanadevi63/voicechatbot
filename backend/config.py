from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    # Small, fast model used only to classify intent before retrieval
    router_model: str = "openai/gpt-oss-20b"

    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    milvus_db_path: str = "./data/milvus.db"
    collection_name: str = "docs_rag"
    docs_dir: str = "./docs"

    chunk_size: int = 1000
    chunk_overlap: int = 150
    top_k: int = 4

    # Comma-separated browser origins allowed to call the API from another domain,
    # e.g. "https://app.example.com". Empty = same-origin only (Vite proxy in dev,
    # FastAPI serving frontend/dist in prod), which needs no CORS.
    cors_origins: str = ""

    # Voice (Deepgram). Empty key = voice endpoints disabled, text chat still works.
    deepgram_api_key: str = ""
    deepgram_stt_model: str = "nova-3"
    deepgram_tts_model: str = "aura-2-thalia-en"
    # Optional comma-separated domain terms to boost in transcription (Nova-3 keyterm prompting)
    deepgram_keyterms: str = ""

    # Hands-free voice (WebSocket /api/ws/voice)
    deepgram_flux_model: str = "flux-general-en"
    # Flux end-of-turn confidence (0.5-0.9): higher waits longer before answering
    deepgram_eot_threshold: float = 0.7
    # Max simultaneous hands-free sessions: each one streams paid STT for as long as it's open
    voice_max_sessions: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
