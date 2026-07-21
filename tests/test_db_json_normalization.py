import db_utils


def test_python_booleans_remain_json_booleans():
    payload = {"candidate": False, "mandatory": True}
    normalized = db_utils._normalize_for_json(payload)
    assert normalized["candidate"] is False
    assert normalized["mandatory"] is True


def test_numeric_values_are_still_plain_numbers():
    payload = {"confirmations": 5, "score": 0.3333333333}
    normalized = db_utils._normalize_for_json(payload)
    assert normalized["confirmations"] == 5.0
    assert normalized["score"] == 0.3333333333
