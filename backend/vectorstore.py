from functools import lru_cache
from pathlib import Path

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_milvus import Milvus

from backend.config import get_settings


@lru_cache
def get_embeddings() -> HuggingFaceEmbeddings:
    settings = get_settings()
    return HuggingFaceEmbeddings(
        model_name=settings.embedding_model,
        encode_kwargs={"normalize_embeddings": True},
    )


def get_vectorstore(drop_old: bool = False) -> Milvus:
    settings = get_settings()
    Path(settings.milvus_db_path).parent.mkdir(parents=True, exist_ok=True)
    return Milvus(
        embedding_function=get_embeddings(),
        collection_name=settings.collection_name,
        connection_args={"uri": settings.milvus_db_path},
        index_params={"index_type": "FLAT", "metric_type": "COSINE"},
        auto_id=True,
        drop_old=drop_old,
    )
