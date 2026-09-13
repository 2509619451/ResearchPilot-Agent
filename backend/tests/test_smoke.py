from app.services.retrieval_service import tokens
from app.services.search_service import deduplicate


def test_tokens():
    assert "transformer" in tokens("Transformer for forecasting")


def test_dedup():
    rows = [
        {"title":"A", "doi":"10/x", "relevance_score":0.2},
        {"title":"A2", "doi":"10/x", "relevance_score":0.8},
    ]
    out = deduplicate(rows)
    assert len(out) == 1 and out[0]["title"] == "A2"
