from __future__ import annotations

from hypo_agent.skills.subscription.wbi import get_mixin_key, sign_params


def test_get_mixin_key_matches_verified_report_sample() -> None:
    img_key = "7cd084941338484aae1ad9425b84077c"
    sub_key = "4932caff0ff746eab6f01bf08b70ac45"

    assert get_mixin_key(img_key, sub_key) == "ea1db124af3c7062474693fa704f4ff8"


def test_sign_params_generates_expected_wrid(monkeypatch) -> None:
    monkeypatch.setattr("hypo_agent.skills.subscription.wbi.time.time", lambda: 1712700000)

    signed = sign_params(
        {"mid": "546195", "ps": 10, "pn": 1},
        img_key="7cd084941338484aae1ad9425b84077c",
        sub_key="4932caff0ff746eab6f01bf08b70ac45",
    )

    assert signed["wts"] == 1712700000
    assert signed["w_rid"] == "988bf08d435a895443341751906ef989"

