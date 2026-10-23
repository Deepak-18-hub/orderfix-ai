"""Review card using official ACP MetadataContent; text commands always remain usable."""
import json

from uagents_core.contrib.protocols.chat import MetadataContent

from .locales import tr
from .models import Review


def review_card(review: Review, language: str) -> MetadataContent:
    labels = {
        "en": ("Review return request", "Order", "Item", "Quantity", "Reason", "Confirm request", "Cancel"),
        "hi": ("वापसी अनुरोध की समीक्षा", "ऑर्डर", "उत्पाद", "संख्या", "कारण", "अनुरोध की पुष्टि", "रद्द करें"),
        "kn": ("ವಾಪಸಾತಿ ವಿನಂತಿ ಪರಿಶೀಲಿಸಿ", "ಆರ್ಡರ್", "ವಸ್ತು", "ಪ್ರಮಾಣ", "ಕಾರಣ", "ವಿನಂತಿ ದೃಢೀಕರಿಸಿ", "ರದ್ದುಮಾಡಿ"),
    }[language]
    payload = {"title": labels[0], "summary_rows": [
        {"label": labels[1], "value": review.order_number},
        {"label": labels[2], "value": review.item_title},
        {"label": labels[3], "value": str(review.quantity)},
        {"label": labels[4], "value": tr(language, "reason_" + review.reason)},
        {"label": "ID", "value": review.token},
        {"label": "Mode", "value": review.mode.upper()},
    ], "approve_cta": {"label": labels[5], "primary": True,
        "selection": {"action": "confirm", "review_token": review.token}},
        "reject_cta": {"label": labels[6], "selection": {"action": "cancel", "review_token": review.token}}}
    return MetadataContent(metadata={"card_protocol_version": "1", "requires_card_interaction": "true",
        "card_kind": "review", "card_payload": json.dumps(payload, ensure_ascii=False)})
