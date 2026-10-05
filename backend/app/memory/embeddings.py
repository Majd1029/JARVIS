"""Text embeddings from a local Ollama model (free; Anthropic has no embeddings API)."""

import json
import urllib.error
import urllib.request

EMBEDDING_DIMS = 768  # nomic-embed-text; the database columns are sized to match

# nomic-embed-text is trained with task prefixes and retrieves noticeably better with them.
_PREFIXES = {"nomic-embed-text": ("search_query: ", "search_document: ")}


class EmbeddingError(Exception):
    pass


class Embedder:
    def __init__(self, ollama_url: str, model: str = "nomic-embed-text"):
        self.url = f"{ollama_url}/api/embed"
        self.model = model
        self.query_prefix, self.document_prefix = _PREFIXES.get(model.split(":")[0], ("", ""))

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self.query_prefix + text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([self.document_prefix + t for t in texts]) if texts else []

    def _embed(self, texts: list[str]) -> list[list[float]]:
        body = {
            "model": self.model,
            "input": texts,
            # Run on the CPU: the chat model needs the whole GPU, and loading this one there
            # would make Ollama swap models on every message.
            "options": {"num_gpu": 0},
        }
        request = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                embeddings = json.loads(response.read())["embeddings"]
        except urllib.error.HTTPError as e:
            raise EmbeddingError(f"Ollama embedding failed ({e.code}): {e.read().decode(errors='replace')}") from e
        except OSError as e:
            raise EmbeddingError(f"Ollama not reachable for embeddings: {e}") from e
        if embeddings and len(embeddings[0]) != EMBEDDING_DIMS:
            raise EmbeddingError(
                f"{self.model} returns {len(embeddings[0])}-dimensional vectors; the database expects "
                f"{EMBEDDING_DIMS}. Use nomic-embed-text or change EMBEDDING_DIMS and migrate."
            )
        return embeddings
