from pmcal import config, polymarket


def market(**overrides):
    m = {
        "id": 1,
        "question": "Will X happen?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["1", "0"]',
        "umaResolutionStatus": "resolved",
        "clobTokenIds": '["111", "222"]',
        "startDate": "2025-01-01T00:00:00Z",
        "endDate": "2025-03-01T12:00:00Z",
        "closedTime": "2025-03-03 18:30:50+00",
        "volumeNum": 50_000,
    }
    return {**m, **overrides}


def test_resolved_outcome():
    assert polymarket.resolved_outcome(market()) == 1
    assert polymarket.resolved_outcome(market(outcomePrices='["0", "1"]')) == 0
    assert polymarket.resolved_outcome(market(outcomePrices='["0.5", "0.5"]')) is None
    assert polymarket.resolved_outcome(market(outcomes='["Trump", "Harris"]')) is None
    assert polymarket.resolved_outcome(market(umaResolutionStatus="disputed")) is None


def test_slim_market_uses_earlier_of_end_and_closed_time():
    slim = polymarket.slim_market(market(), {"id": 9}, "Politics")
    assert slim["close_time"].startswith("2025-03-01T12:00:00")
    assert slim["yes_token"] == "111"
    early_close = market(closedTime="2025-02-20 10:00:00+00")
    assert polymarket.slim_market(early_close, {"id": 9}, "Politics")["close_time"].startswith(
        "2025-02-20T10:00:00"
    )


def test_slim_market_rejects_thin_markets():
    assert polymarket.slim_market(market(volumeNum=10), {"id": 9}, "Politics") is None


def test_categorize_uses_priority_order():
    assert polymarket.categorize({"sports", "politics"}) == "Sports"
    assert polymarket.categorize({"trump", "economy"}) == "Economics"
    assert polymarket.categorize({"misc"}) == "Other"


def test_event_markets_caps_per_event():
    event = {
        "id": 5,
        "tags": [{"slug": "politics"}],
        "markets": [market(id=i) for i in range(25)],
    }
    out = polymarket.event_markets(event)
    assert len(out) == config.POLYMARKET_MAX_MARKETS_PER_EVENT
    assert out == polymarket.event_markets(event)
