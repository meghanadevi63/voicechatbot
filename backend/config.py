from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    # Small, fast model used only to classify intent before retrieval
    router_model: str = "openai/gpt-oss-20b"

    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    milvus_db_path: str = "./data/milvus.db"
    collection_name: str = "docs_rag"
    docs_dir: str = "./docs"

    chunk_size: int = 1000
    chunk_overlap: int = 150
    top_k: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
