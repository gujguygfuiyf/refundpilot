from app.policy import decide, load_policy_files, status_for

BROKEN = {"product_match": "likely", "damage_type": "broken", "severity": "severe", "visible_damage": True,
          "evidence_quality": "sufficient", "stock_photo_suspected": False, "description_consistent": "yes",
          "needs_human_review": False, "confidence": 0.9, "observations": [], "limitations": []}


def policies():
    return {p["version"]: p for p in load_policy_files()}


def test_policy_files_load():
    ps = policies()
    assert {"standard-1.1", "strict-1.0", "goodwill-1.1"} <= set(ps)


def test_broken_full_refund_standard():
    d = decide(policies()["standard-1.1"], BROKEN, order_cents=1900, remaining_cents=1900, days_since_delivery=3, prior_claims=0)
    assert d.outcome == "FULL_REFUND" and d.suggested_cents == 1900 and d.rule_id == "R1-broken"
    assert d.requires_approval is True            # auto_approve_below_cents = 0
    assert status_for(d) == "REVIEW_REQUIRED"


def test_cosmetic_partial_capped_by_remaining():
    a = dict(BROKEN, damage_type="cosmetic", severity="minor")
    d = decide(policies()["standard-1.1"], a, order_cents=1900, remaining_cents=300, days_since_delivery=3, prior_claims=0)
    assert d.outcome == "PARTIAL_REFUND" and d.suggested_cents == 300     # 30% of 1900 = 570, capped at 300


def test_same_evidence_three_policies_differ():
    a = dict(BROKEN, damage_type="cosmetic", severity="minor")
    ps = policies()
    got = {v: decide(ps[v], a, order_cents=10000, remaining_cents=10000, days_since_delivery=3, prior_claims=0).suggested_cents
           for v in ("standard-1.1", "strict-1.0", "goodwill-1.1")}
    assert got == {"standard-1.1": 3000, "strict-1.0": 1500, "goodwill-1.1": 5000}


def test_mismatch_requests_evidence_or_rejects():
    a = dict(BROKEN, product_match="mismatch")
    ps = policies()
    assert decide(ps["standard-1.1"], a, order_cents=1900, remaining_cents=1900, days_since_delivery=3, prior_claims=0).outcome == "REQUEST_EVIDENCE"
    assert decide(ps["strict-1.0"], a, order_cents=1900, remaining_cents=1900, days_since_delivery=3, prior_claims=0).outcome == "REJECT"


def test_window_and_repeat_prechecks():
    ps = policies()
    late = decide(ps["standard-1.1"], BROKEN, order_cents=1900, remaining_cents=1900, days_since_delivery=20, prior_claims=0)
    assert late.outcome == "REVIEW" and late.rule_id == "PRE-window" and late.suggested_cents == 0
    rep = decide(ps["strict-1.0"], BROKEN, order_cents=1900, remaining_cents=1900, days_since_delivery=2, prior_claims=1)
    assert rep.outcome == "REJECT" and rep.rule_id == "PRE-repeat"


def test_strict_human_review_flag():
    a = dict(BROKEN, needs_human_review=True)
    d = decide(policies()["strict-1.0"], a, order_cents=1900, remaining_cents=1900, days_since_delivery=2, prior_claims=0)
    assert d.outcome == "REVIEW" and d.rule_id == "PRE-ai-flag"


def test_nothing_left_rejects():
    d = decide(policies()["standard-1.1"], BROKEN, order_cents=1900, remaining_cents=0, days_since_delivery=2, prior_claims=0)
    assert d.outcome == "REJECT"


def test_photo_of_another_product_asks_for_evidence_before_wrong_item_review():
    """Demo case B: the model tends to report a different product as damage_type=wrong_item AND
    product_match=mismatch. The mismatch must win (no money, ask for evidence), so R4 is checked before R3."""
    a = dict(BROKEN, product_match="mismatch", damage_type="wrong_item")
    ps = policies()
    for v in ("standard-1.1", "goodwill-1.1"):
        d = decide(ps[v], a, order_cents=4900, remaining_cents=4900, days_since_delivery=3, prior_claims=0)
        assert (d.rule_id, d.outcome, d.suggested_cents) == ("R4-mismatch", "REQUEST_EVIDENCE", 0)
    assert decide(ps["strict-1.0"], a, order_cents=4900, remaining_cents=4900, days_since_delivery=3,
                  prior_claims=0).outcome == "REJECT"


def test_not_as_described_on_the_right_product_still_goes_to_review():
    a = dict(BROKEN, damage_type="not_as_described")
    d = decide(policies()["standard-1.1"], a, order_cents=4900, remaining_cents=4900, days_since_delivery=3, prior_claims=0)
    assert (d.rule_id, d.outcome) == ("R3-wrong", "REVIEW")
