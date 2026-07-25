from hyperliquid_v2.market_data.account_equity import (
    enrich_account_state,
    extract_spot_usdc,
    resolve_equity,
    resolution_from_account_state,
)


ZERO_PERP = {
    "marginSummary": {"accountValue": "0.0"},
    "withdrawable": "0.0",
}
SPOT_USDC = {
    "balances": [
        {"coin": "USDC", "total": "2974.78", "hold": "74.78"}
    ]
}


def test_unified_account_uses_spot_usdc_instead_of_zero_perp_view():
    resolution = resolve_equity(
        ZERO_PERP,
        SPOT_USDC,
        "unifiedAccount",
    )

    assert resolution.equity_usd == 2974.78
    assert resolution.available_usd == 2900.0
    assert resolution.is_degenerate is False
    assert resolution.source.startswith("spotClearinghouseState")


def test_portfolio_margin_and_unknown_mode_zero_perp_are_defensive():
    portfolio = resolve_equity(
        ZERO_PERP,
        SPOT_USDC,
        "portfolioMargin",
    )
    fallback = resolve_equity(ZERO_PERP, SPOT_USDC, "unknown")

    assert portfolio.equity_usd == 2974.78
    assert fallback.equity_usd == 2974.78
    assert "fallback_perp_zero" in fallback.source


def test_standard_account_does_not_double_count_spot_balance():
    perp = {
        "marginSummary": {"accountValue": "1500"},
        "withdrawable": "1200",
    }

    resolution = resolve_equity(perp, SPOT_USDC, "standard")

    assert resolution.equity_usd == 1500.0
    assert resolution.available_usd == 1200.0
    assert resolution.source == "marginSummary.accountValue"

    zero_standard = resolve_equity(ZERO_PERP, SPOT_USDC, "standard")
    assert zero_standard.equity_usd == 0.0


def test_attached_resolution_is_used_by_compatibility_wrapper():
    state = enrich_account_state(
        ZERO_PERP,
        SPOT_USDC,
        "unifiedAccount",
    )

    assert resolution_from_account_state(state).equity_usd == 2974.78


def test_malformed_spot_payload_is_fail_closed_and_auditable():
    for payload in (None, {}, {"balances": "bad"}, {"balances": [None]}):
        assert extract_spot_usdc(payload)[0] is False

    resolution = resolve_equity(None, None, "unifiedAccount")
    assert resolution.is_degenerate is True
    assert "resolved_equity_is_zero" in resolution.warnings
