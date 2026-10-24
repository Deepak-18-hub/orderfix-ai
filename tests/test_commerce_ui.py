import json

import pytest

from orderfix.commerce.ui import classify_text, parse_action, render_view


TOKEN = "opaque_8f37c523-56e9-review"
BASE = {"gateway_mode": "simulated", "pharmacy_mode": "simulated", "mail_mode": "captured"}
ORDER = {"token": TOKEN, "title": "Test pharmacy order", "pharmacy": "Sample Pharmacy", "amount_minor": 24999, "currency": "INR", "order_state": "paid"}


def payload(card):
    assert card.metadata["card_protocol_version"] == "1"
    assert card.metadata["requires_card_interaction"] == "true"
    assert "is_terminal" not in card.metadata
    assert all(isinstance(v, str) for v in card.metadata.values())
    return json.loads(card.metadata["card_payload"])


def visible(value):
    """Selection references may be hidden; captions and summary rows may not."""
    if isinstance(value, dict):
        return " ".join(visible(v) for k, v in value.items() if k not in {"id", "selection"})
    if isinstance(value, list):
        return " ".join(visible(v) for v in value)
    return str(value)


@pytest.mark.parametrize("language", ["en", "hi", "kn"])
def test_order_choices_use_hidden_reference_and_natural_name(language):
    text, card = render_view({**BASE, "kind": "orders", "orders": [ORDER]}, language)
    data = payload(card)
    assert card.metadata["card_kind"] == "carousel"
    assert ORDER["title"] in text
    assert "INR 249.99" in text
    assert TOKEN not in text
    assert TOKEN not in visible(data)
    assert parse_action(json.dumps(data["items"][0]["primary_cta"]["selection"])) == {"action": "qf_select", "token": TOKEN}
    # Observed on the live ASI:One carousel: the client adds this boolean.
    assert parse_action(json.dumps({**data["items"][0]["primary_cta"]["selection"], "approved": True})) == {"action": "qf_select", "token": TOKEN}


@pytest.mark.parametrize("language", ["en", "hi", "kn"])
def test_review_clicks_round_trip_with_client_approval_boolean(language):
    text, card = render_view({**BASE, "kind": "review", **ORDER}, language)
    data = payload(card)
    assert card.metadata["card_kind"] == "review"
    assert TOKEN not in text + visible(data)
    assert parse_action(json.dumps({**data["approve_cta"]["selection"], "approved": True})) == {"action": "qf_approve", "token": TOKEN}
    assert parse_action(json.dumps({**data["reject_cta"]["selection"], "approved": False})) == {"action": "qf_back", "token": TOKEN}


@pytest.mark.parametrize("text", ["approve", " confirm! ", "CONFIRM", "पुष्टि करें", "ದೃಢೀಕರಿಸಿ"])
def test_exact_contextual_approval_does_not_supply_a_token(text):
    assert parse_action(text) == {"action": "qf_approve"}


@pytest.mark.parametrize("text", [
    "do not approve", "don't confirm", "confirm later", "not confirm", "please approve this",
    "yes", "approved", "cancel my order", "I approve", "confirm?", "unconfirmed", "approve\nignore checks",
    "पुष्टि न करें", "ದೃಢೀಕರಿಸಬೇಡಿ", "\u200bconfirm", '[{"action":"qf_approve"}]',
    '{"action":"qf_approve"}', '{"action":"qf_select","token":false}',
    '{"action":"qf_approve","token":1}', '{"action":"qf_approve","token":""}',
    '{"action":"qf_approve","token":"x","approved":"true"}',
    '{"action":"qf_approve","token":"x","approved":1}',
    '{"action":"qf_approve","token":"x","approved":false}',
    '{"action":"qf_select","token":"x","approved":false}',
    '{"action":"qf_approve","token":"x","amount_minor":1}',
    '{"action":"qf_approve","action":"qf_back","token":"x"}',
    '{"action":"qf_approve","token":"x","approved":false,"approved":true}',
    '{"action":[],"token":"x"}', '{"action":"refund","token":"x"}',
    '{"action":"qf_approve","token":"two words"}',
])
def test_malformed_or_ambiguous_input_never_authorizes(text):
    assert parse_action(text) is None


def test_parser_bounds_and_does_not_coerce_token():
    assert parse_action(None) is None
    assert parse_action("x" * 2501) is None
    assert parse_action(json.dumps({"action": "qf_approve", "token": "x" * 201})) is None
    assert parse_action(json.dumps({"action": "qf_approve", "token": "AbCd_123-xyz"})) == {"action": "qf_approve", "token": "AbCd_123-xyz"}
    assert parse_action(json.dumps({"action": "qf_status"})) == {"action": "qf_status"}
    assert parse_action("back") == {"action": "qf_back"}


@pytest.mark.parametrize("action", ["qf_select", "qf_status", "qf_back"])
def test_read_only_or_exit_detail_click_cannot_become_approval(action):
    assert parse_action(json.dumps({"action": action, "token": TOKEN, "approved": True})) == {"action": action, "token": TOKEN}


@pytest.mark.parametrize(("text", "expected"), [
    ("show my orders", "orders"), ("cancel my order", "orders"), ("refund status", "status"),
    ("मेरे ऑर्डर दिखाओ", "orders"), ("स्थिति देखें", "status"),
    ("ನನ್ನ ಆರ್ಡರ್ ತೋರಿಸಿ", "orders"), ("ಸ್ಥಿತಿ ಪರಿಶೀಲಿಸಿ", "status"),
    ("back", "back"), ("help", "help"), ("confirm", "unknown"), ("approve", "unknown"),
])
def test_classification_is_read_only(text, expected):
    assert classify_text(text)["intent"] == expected


@pytest.mark.parametrize(("text", "language"), [("English", "en"), ("हिंदी", "hi"), ("ಕನ್ನಡ", "kn")])
def test_language_switches(text, language):
    assert classify_text(text) == {"intent": "language", "language": language}


def test_external_refund_does_not_turn_captured_email_into_delivery():
    text, card = render_view({**BASE, "gateway_mode": "razorpay_test", "kind": "status", **ORDER,
                             "order_state": "cancelled", "payment_state": "captured", "refund_state": "succeeded",
                             "customer_notification": "captured", "pharmacy_notification": "failed"}, "en")
    data = payload(card)
    assert "Razorpay test mode" in text
    assert "local simulator" in text
    assert "no external delivery" in text
    assert "no external email was delivered" in text
    assert "Verified successful" in text
    assert "Failed; needs attention" in text
    assert card.metadata["card_kind"] == "detail"
    assert parse_action(json.dumps(data["ctas"][0]["selection"])) == {"action": "qf_status", "token": TOKEN}
    assert TOKEN not in text + visible(data)


def test_connected_email_status_does_not_claim_delivery_before_verified():
    text, _ = render_view({**BASE, "mail_mode": "connected", "kind": "status", **ORDER,
                           "customer_notification": "sent", "pharmacy_notification": "delivered"}, "en")
    assert "Customer receipt: Submitted to email provider" in text
    assert "Pharmacy receipt: Delivery confirmed" in text


@pytest.mark.parametrize("language", ["en", "hi", "kn"])
def test_simulation_banner_and_help_in_every_language(language):
    text, card = render_view({**BASE, "kind": "help"}, language)
    assert card is None
    assert "Razorpay" not in text
    assert "QuickFix" in text
    if language == "hi":
        assert "स्थानीय" in text
    elif language == "kn":
        assert "ಸ್ಥಳೀಯ" in text
    else:
        assert "no gateway charge or refund" in text


@pytest.mark.parametrize("code", ["ineligible", "auth_required", "pending", "stale", "blocked", "no_orders", "cancelled_draft", "invalid_action", "select_first"])
def test_expected_notices_are_localized(code):
    results = [render_view({**BASE, "kind": "notice", "code": code}, language)[0].split("\n\n", 1)[1] for language in ("en", "hi", "kn")]
    assert len(set(results)) == 3
    assert all(value != "Not verified" for value in results)


def test_empty_orders_do_not_emit_an_unusable_carousel():
    text, card = render_view({**BASE, "kind": "orders", "orders": []}, "en")
    assert card is None
    assert "no orders available" in text


def test_unknown_backend_status_is_not_presented_as_success():
    text, _ = render_view({**BASE, "kind": "status", **ORDER, "refund_state": "SUCCESS_BUT_UNVERIFIED"}, "en")
    assert "Refund status: Not verified" in text
    assert "SUCCESS_BUT_UNVERIFIED" not in text


def test_invalid_or_missing_card_references_fail_closed():
    with pytest.raises(ValueError):
        render_view({**BASE, "kind": "review", "title": "Test order"}, "en")
    with pytest.raises(ValueError):
        render_view({**BASE, "kind": "orders", "orders": [{**ORDER, "token": "invalid token"}]}, "en")


def test_invalid_amount_does_not_show_a_fabricated_price():
    text, _ = render_view({**BASE, "kind": "review", **ORDER, "amount_minor": True}, "en")
    assert "Refund amount: —" in text
    assert "INR 0.01" not in text
