import news_feed
import sentiment
from news_feed import canonicalize_url
from news_sentiment_shadow import (
    classify_news_item,
    exact_news_hash,
    exact_sentiment_hash,
    sentiment_direction,
)


def test_canonical_url_removes_tracking_parameters_and_fragment():
    left = canonicalize_url(
        "https://Example.com/news/bitcoin/?utm_source=x&ref=abc&id=7#section"
    )
    right = canonicalize_url("https://example.com/news/bitcoin?id=7")
    assert left == right


def test_exact_news_hash_is_stable_for_tracking_variants():
    base = {
        "source": "coinjournal",
        "guid": "",
        "title": "Bitcoin ETF inflows rise",
        "published_at": "2026-07-22T10:00:00+00:00",
    }
    first = {
        **base,
        "url": "https://coinjournal.net/news/bitcoin-etf/?utm_source=rss",
    }
    second = {
        **base,
        "url": "https://coinjournal.net/news/bitcoin-etf",
    }
    assert exact_news_hash(first) == exact_news_hash(second)


def test_bullish_bitcoin_etf_news_is_relevant_and_directional():
    classified = classify_news_item(
        {
            "title": "Bitcoin ETF inflows surge as institutional adoption expands",
            "summary": "BlackRock records strong buying and record demand.",
        }
    )
    assert classified["assets"] == ["BTC"]
    assert classified["event_category"] == "etf"
    assert classified["relevance_score"] >= 0.60
    assert classified["direction_score"] > 0


def test_market_wide_macro_news_maps_to_all_assets():
    classified = classify_news_item(
        {
            "title": "Federal Reserve signals rate hike after inflation surprise",
            "summary": "Risk assets face renewed selling pressure.",
        }
    )
    assert classified["assets"] == ["BTC", "ETH", "SOL"]
    assert classified["event_category"] == "macro"
    assert classified["direction_score"] < 0


def test_sentiment_hash_deduplicates_same_provider_timestamp():
    payload = {"valore": 35, "classificazione": "Fear", "timestamp": 1784707200}
    assert exact_sentiment_hash("cmc", payload) == exact_sentiment_hash("cmc", dict(payload))


def test_fear_and_greed_contrarian_hypothesis_is_shadow_only_mapping():
    assert sentiment_direction(20) == 1.0
    assert sentiment_direction(35) == 0.5
    assert sentiment_direction(50) == 0.0
    assert sentiment_direction(65) == -0.5
    assert sentiment_direction(80) == -1.0


def test_news_are_not_fetched_for_live_prompt_in_shadow_only_mode(monkeypatch):
    monkeypatch.setenv("NEWS_SENTIMENT_SHADOW_ONLY", "true")

    def unexpected_fetch(*args, **kwargs):
        raise AssertionError("live prompt must not fetch raw news in shadow-only mode")

    monkeypatch.setattr(news_feed, "fetch_news_items", unexpected_fetch)
    text = news_feed.fetch_latest_news()
    assert "NEWS SHADOW ONLY" in text
    assert "non inferire direzione" in text


def test_sentiment_is_not_fetched_for_live_prompt_in_shadow_only_mode(monkeypatch):
    monkeypatch.setenv("NEWS_SENTIMENT_SHADOW_ONLY", "true")

    def unexpected_fetch():
        raise AssertionError("live prompt must not fetch raw sentiment in shadow-only mode")

    monkeypatch.setattr(sentiment, "get_latest_fear_and_greed", unexpected_fetch)
    text, payload = sentiment.get_sentiment()
    assert "SENTIMENT SHADOW ONLY" in text
    assert payload is None
