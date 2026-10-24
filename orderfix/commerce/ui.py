"""QuickFix's deterministic, multilingual chat/card boundary.

Selections carry opaque references; visible copy never asks users to transcribe
them. Parsing an approval expresses intent only: the engine must bind it to the
current customer's unexpired immutable review before executing any operation.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from uagents_core.contrib.protocols.chat import MetadataContent

from ..intent import _language


_LABELS = {
    "en": {
        "help": "QuickFix helps cancel a pharmacy order and track its refund. Say ‘show my orders’, choose an order, then review the cancellation. You can ask for status at any time.",
        "orders": "Choose your order", "orders_hint": "Select an order below, or reply with its product name. No order number is needed.",
        "empty": "There are no orders available for this account.", "select": "Review cancellation", "view_order": "View order", "order_detail": "Order details", "order_total": "Order total",
        "review": "Review cancellation and refund", "review_hint": "Check the details below. Choose ‘Cancel and refund’ to approve, or ‘Go back’ to leave this review. You can also reply ‘confirm’ or ‘back’.",
        "approve": "Cancel and refund", "back": "Go back", "status": "Cancellation and refund status", "check": "Check status",
        "item": "Order", "pharmacy": "Pharmacy", "amount": "Refund amount", "destination": "Refund destination", "original": "Original payment method",
        "terms": "Cancellation terms", "expires": "Review expires", "customer_email": "Customer receipt", "pharmacy_email": "Pharmacy receipt",
        "order_state": "Order status", "payment_state": "Payment status", "refund_state": "Refund status", "stage": "Case progress", "unknown": "Not verified",
        "demo": "LOCAL SIMULATION: payments and refunds are simulated. Email capture is not external delivery.",
        "sandbox": "GATEWAY TEST MODE: test payments and refunds move no real money. Notification status is shown separately.",
        "notice": "QuickFix update", "captured_hint": "Captured means saved to the test mailbox; no external email was delivered.",
        "status_hint": "Ask ‘check status’ to refresh this case, or ‘show my orders’ to choose another order.",
    },
    "hi": {
        "help": "QuickFix फ़ार्मेसी ऑर्डर रद्द करने और रिफंड की स्थिति जानने में मदद करता है। ‘मेरे ऑर्डर दिखाओ’ कहें, ऑर्डर चुनें और रद्द करने की जानकारी देखें। आप कभी भी स्थिति पूछ सकते हैं।",
        "orders": "अपना ऑर्डर चुनें", "orders_hint": "नीचे ऑर्डर चुनें या उत्पाद का नाम लिखें। ऑर्डर नंबर की ज़रूरत नहीं है।",
        "empty": "इस खाते के लिए कोई ऑर्डर उपलब्ध नहीं है।", "select": "रद्द करने की समीक्षा", "view_order": "ऑर्डर देखें", "order_detail": "ऑर्डर का विवरण", "order_total": "ऑर्डर की कुल राशि",
        "review": "रद्द करने और रिफंड की समीक्षा", "review_hint": "नीचे की जानकारी देखें। सहमति देने के लिए ‘रद्द करें और रिफंड लें’ चुनें, या ‘वापस जाएँ’ चुनें। आप ‘पुष्टि करें’ या ‘वापस’ भी लिख सकते हैं।",
        "approve": "रद्द करें और रिफंड लें", "back": "वापस जाएँ", "status": "रद्द करने और रिफंड की स्थिति", "check": "स्थिति देखें",
        "item": "ऑर्डर", "pharmacy": "फ़ार्मेसी", "amount": "रिफंड राशि", "destination": "रिफंड का माध्यम", "original": "मूल भुगतान माध्यम",
        "terms": "रद्द करने की शर्तें", "expires": "समीक्षा की समय-सीमा", "customer_email": "ग्राहक की रसीद", "pharmacy_email": "फ़ार्मेसी की रसीद",
        "order_state": "ऑर्डर की स्थिति", "payment_state": "भुगतान की स्थिति", "refund_state": "रिफंड की स्थिति", "stage": "मामले की प्रगति", "unknown": "सत्यापन नहीं हुआ",
        "demo": "स्थानीय सिमुलेशन: भुगतान और रिफंड का अनुकरण किया जाता है। ईमेल कैप्चर का मतलब बाहरी ईमेल भेजना नहीं है।",
        "sandbox": "गेटवे परीक्षण मोड: परीक्षण भुगतान और रिफंड में असली पैसे नहीं चलते। सूचनाओं की स्थिति अलग दिखाई गई है।",
        "notice": "QuickFix की जानकारी", "captured_hint": "कैप्चर का मतलब परीक्षण मेलबॉक्स में सहेजा गया; कोई बाहरी ईमेल नहीं पहुँचा।",
        "status_hint": "इस मामले की ताज़ा जानकारी के लिए ‘स्थिति देखें’ या दूसरा ऑर्डर चुनने के लिए ‘मेरे ऑर्डर दिखाओ’ कहें।",
    },
    "kn": {
        "help": "QuickFix ಔಷಧಾಲಯದ ಆರ್ಡರ್ ರದ್ದು ಮಾಡಲು ಮತ್ತು ಮರುಪಾವತಿ ಸ್ಥಿತಿ ತಿಳಿಯಲು ಸಹಾಯ ಮಾಡುತ್ತದೆ. ‘ನನ್ನ ಆರ್ಡರ್ ತೋರಿಸಿ’ ಎಂದು ಹೇಳಿ, ಆರ್ಡರ್ ಆಯ್ಕೆಮಾಡಿ ಮತ್ತು ರದ್ದತಿಯನ್ನು ಪರಿಶೀಲಿಸಿ. ಯಾವಾಗ ಬೇಕಾದರೂ ಸ್ಥಿತಿ ಕೇಳಬಹುದು.",
        "orders": "ನಿಮ್ಮ ಆರ್ಡರ್ ಆಯ್ಕೆಮಾಡಿ", "orders_hint": "ಕೆಳಗೆ ಆರ್ಡರ್ ಆಯ್ಕೆಮಾಡಿ ಅಥವಾ ಉತ್ಪನ್ನದ ಹೆಸರು ಬರೆಯಿರಿ. ಆರ್ಡರ್ ಸಂಖ್ಯೆ ಅಗತ್ಯವಿಲ್ಲ.",
        "empty": "ಈ ಖಾತೆಗೆ ಯಾವುದೇ ಆರ್ಡರ್ ಲಭ್ಯವಿಲ್ಲ.", "select": "ರದ್ದತಿ ಪರಿಶೀಲಿಸಿ", "view_order": "ಆರ್ಡರ್ ನೋಡಿ", "order_detail": "ಆರ್ಡರ್ ವಿವರಗಳು", "order_total": "ಆರ್ಡರ್ ಒಟ್ಟು ಮೊತ್ತ",
        "review": "ರದ್ದತಿ ಮತ್ತು ಮರುಪಾವತಿ ಪರಿಶೀಲನೆ", "review_hint": "ಕೆಳಗಿನ ವಿವರಗಳನ್ನು ಪರಿಶೀಲಿಸಿ. ಒಪ್ಪಲು ‘ರದ್ದು ಮಾಡಿ ಮತ್ತು ಮರುಪಾವತಿ ಪಡೆಯಿರಿ’ ಆಯ್ಕೆಮಾಡಿ, ಅಥವಾ ‘ಹಿಂದೆ ಹೋಗಿ’ ಆಯ್ಕೆಮಾಡಿ. ‘ದೃಢೀಕರಿಸಿ’ ಅಥವಾ ‘ಹಿಂದೆ’ ಎಂದೂ ಬರೆಯಬಹುದು.",
        "approve": "ರದ್ದು ಮಾಡಿ ಮತ್ತು ಮರುಪಾವತಿ ಪಡೆಯಿರಿ", "back": "ಹಿಂದೆ ಹೋಗಿ", "status": "ರದ್ದತಿ ಮತ್ತು ಮರುಪಾವತಿ ಸ್ಥಿತಿ", "check": "ಸ್ಥಿತಿ ಪರಿಶೀಲಿಸಿ",
        "item": "ಆರ್ಡರ್", "pharmacy": "ಔಷಧಾಲಯ", "amount": "ಮರುಪಾವತಿ ಮೊತ್ತ", "destination": "ಮರುಪಾವತಿ ವಿಧಾನ", "original": "ಮೂಲ ಪಾವತಿ ವಿಧಾನ",
        "terms": "ರದ್ದತಿ ಷರತ್ತುಗಳು", "expires": "ಪರಿಶೀಲನೆಯ ಗಡುವು", "customer_email": "ಗ್ರಾಹಕರ ರಸೀದಿ", "pharmacy_email": "ಔಷಧಾಲಯದ ರಸೀದಿ",
        "order_state": "ಆರ್ಡರ್ ಸ್ಥಿತಿ", "payment_state": "ಪಾವತಿ ಸ್ಥಿತಿ", "refund_state": "ಮರುಪಾವತಿ ಸ್ಥಿತಿ", "stage": "ಪ್ರಕರಣದ ಪ್ರಗತಿ", "unknown": "ಪರಿಶೀಲಿಸಲಾಗಿಲ್ಲ",
        "demo": "ಸ್ಥಳೀಯ ಅನುಕರಣೆ: ಪಾವತಿ ಮತ್ತು ಮರುಪಾವತಿ ಅನುಕರಿಸಲಾಗಿದೆ. ಇಮೇಲ್ ದಾಖಲಿಸುವುದು ಹೊರಗೆ ತಲುಪಿಸುವುದಲ್ಲ.",
        "sandbox": "ಗೇಟ್‌ವೇ ಪರೀಕ್ಷಾ ಮೋಡ್: ಪರೀಕ್ಷಾ ಪಾವತಿ ಮತ್ತು ಮರುಪಾವತಿಯಲ್ಲಿ ನಿಜವಾದ ಹಣ ಚಲಿಸುವುದಿಲ್ಲ. ಸೂಚನೆಗಳ ಸ್ಥಿತಿಯನ್ನು ಪ್ರತ್ಯೇಕವಾಗಿ ತೋರಿಸಲಾಗಿದೆ.",
        "notice": "QuickFix ಮಾಹಿತಿ", "captured_hint": "ದಾಖಲಾಗಿದೆ ಎಂದರೆ ಪರೀಕ್ಷಾ ಅಂಚೆಪೆಟ್ಟಿಗೆಯಲ್ಲಿ ಉಳಿಸಲಾಗಿದೆ; ಹೊರಗಿನ ಇಮೇಲ್ ತಲುಪಿಲ್ಲ.",
        "status_hint": "ಈ ಪ್ರಕರಣದ ಹೊಸ ಮಾಹಿತಿಗೆ ‘ಸ್ಥಿತಿ ಪರಿಶೀಲಿಸಿ’ ಅಥವಾ ಬೇರೆ ಆರ್ಡರ್ ಆಯ್ಕೆ ಮಾಡಲು ‘ನನ್ನ ಆರ್ಡರ್ ತೋರಿಸಿ’ ಎಂದು ಹೇಳಿ.",
    },
}

_STATES = {
    "paid": ("Paid", "भुगतान हुआ", "ಪಾವತಿಸಲಾಗಿದೆ"),
    "captured": ("Captured", "कैप्चर हुआ", "ದಾಖಲಾಗಿದೆ"),
    "authorized": ("Authorized", "अधिकृत", "ಅನುಮೋದಿಸಲಾಗಿದೆ"),
    "active": ("Active", "सक्रिय", "ಸಕ್ರಿಯ"),
    "confirmed": ("Confirmed", "पुष्टि हुई", "ದೃಢೀಕರಿಸಲಾಗಿದೆ"),
    "pending": ("Pending", "लंबित", "ಬಾಕಿ ಇದೆ"),
    "queued": ("Queued", "कतार में", "ಸರತಿಯಲ್ಲಿದೆ"),
    "inflight": ("Checking outcome", "परिणाम की जाँच जारी", "ಫಲಿತಾಂಶ ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ"),
    "cancel_pending": ("Cancellation pending", "रद्द करना लंबित", "ರದ್ದತಿ ಬಾಕಿ ಇದೆ"),
    "cancelled": ("Cancelled", "रद्द हुआ", "ರದ್ದಾಗಿದೆ"),
    "canceled": ("Cancelled", "रद्द हुआ", "ರದ್ದಾಗಿದೆ"),
    "not_started": ("Not started", "शुरू नहीं हुआ", "ಪ್ರಾರಂಭವಾಗಿಲ್ಲ"),
    "not_required": ("Not required", "ज़रूरी नहीं", "ಅಗತ್ಯವಿಲ್ಲ"),
    "succeeded": ("Verified successful", "सफलता सत्यापित", "ಯಶಸ್ಸು ಪರಿಶೀಲಿಸಲಾಗಿದೆ"),
    "refunded": ("Refund verified", "रिफंड सत्यापित", "ಮರುಪಾವತಿ ಪರಿಶೀಲಿಸಲಾಗಿದೆ"),
    "failed": ("Failed; needs attention", "विफल; जाँच ज़रूरी", "ವಿಫಲ; ಪರಿಶೀಲನೆ ಅಗತ್ಯ"),
    "rejected": ("Rejected", "अस्वीकृत", "ತಿರಸ್ಕರಿಸಲಾಗಿದೆ"),
    "dispatched": ("Dispatched", "भेज दिया गया", "ರವಾನಿಸಲಾಗಿದೆ"),
    "fulfilled": ("Fulfilled", "पूरा हुआ", "ಪೂರೈಸಲಾಗಿದೆ"),
    "sent": ("Submitted to email provider", "ईमेल सेवा को भेजा", "ಇಮೇಲ್ ಸೇವೆಗೆ ಸಲ್ಲಿಸಲಾಗಿದೆ"),
    "accepted": ("Accepted by provider", "सेवा ने स्वीकार किया", "ಸೇವೆ ಸ್ವೀಕರಿಸಿದೆ"),
    "delivered": ("Delivery confirmed", "डिलीवरी की पुष्टि", "ತಲುಪಿರುವುದು ದೃಢವಾಗಿದೆ"),
    "not_requested": ("Not requested", "अनुरोध नहीं किया", "ಕೋರಲಾಗಿಲ್ಲ"),
    "not_queued": ("Not queued", "कतार में नहीं है", "ಸರತಿಯಲ್ಲಿ ಇಲ್ಲ"),
    "processed": ("Refund processed and verified", "रिफंड पूरा और सत्यापित", "ಮರುಪಾವತಿ ಪೂರ್ಣಗೊಂಡು ಪರಿಶೀಲಿಸಲಾಗಿದೆ"),
    "blocked": ("Verification required", "सत्यापन ज़रूरी", "ಪರಿಶೀಲನೆ ಅಗತ್ಯವಿದೆ"),
    "eligibility": ("Checking pharmacy eligibility", "फ़ार्मेसी की पात्रता जाँच जारी", "ಔಷಧಾಲಯದ ಅರ್ಹತೆ ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ"),
    "payment_check": ("Checking original payment", "मूल भुगतान की जाँच जारी", "ಮೂಲ ಪಾವತಿ ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ"),
    "payment_recheck": ("Rechecking approved amount", "स्वीकृत राशि की फिर जाँच जारी", "ಒಪ್ಪಿದ ಮೊತ್ತವನ್ನು ಮತ್ತೆ ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ"),
    "review": ("Awaiting your approval", "आपकी मंज़ूरी का इंतज़ार", "ನಿಮ್ಮ ಒಪ್ಪಿಗೆಗಾಗಿ ಕಾಯಲಾಗುತ್ತಿದೆ"),
    "cancelling": ("Awaiting pharmacy cancellation", "फ़ार्मेसी के रद्दीकरण का इंतज़ार", "ಔಷಧಾಲಯದ ರದ್ದತಿಗಾಗಿ ಕಾಯಲಾಗುತ್ತಿದೆ"),
    "refund": ("Preparing refund", "रिफंड की तैयारी", "ಮರುಪಾವತಿ ಸಿದ್ಧವಾಗುತ್ತಿದೆ"),
    "submitting": ("Refund submission in progress", "रिफंड अनुरोध जारी", "ಮರುಪಾವತಿ ಸಲ್ಲಿಸಲಾಗುತ್ತಿದೆ"),
    "refund_pending": ("Reconciling refund outcome", "रिफंड के परिणाम की जाँच जारी", "ಮರುಪಾವತಿ ಫಲಿತಾಂಶ ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ"),
    "done": ("Financial operation completed; check receipts below", "वित्तीय कार्रवाई पूरी; नीचे रसीदें देखें", "ಹಣಕಾಸು ಕಾರ್ಯ ಪೂರ್ಣ; ಕೆಳಗೆ ರಸೀದಿ ಪರಿಶೀಲಿಸಿ"),
    "draft_cancelled": ("Draft closed", "प्रारूप बंद", "ಕರಡು ಮುಚ್ಚಲಾಗಿದೆ"),
    "bounced": ("Email delivery failed", "ईमेल डिलीवरी विफल", "ಇಮೇಲ್ ತಲುಪಲಿಲ್ಲ"),
}

_NOTICES = {
    "review_expired": ("That review expired. Choose the order again for fresh details.", "समीक्षा की समय-सीमा पूरी हो गई। ताज़ा जानकारी के लिए ऑर्डर फिर चुनें।", "ಪರಿಶೀಲನೆಯ ಗಡುವು ಮುಗಿದಿದೆ. ಹೊಸ ವಿವರಗಳಿಗೆ ಆರ್ಡರ್ ಮತ್ತೆ ಆಯ್ಕೆಮಾಡಿ."),
    "review_missing": ("There is no current review to approve. Choose an order first.", "पुष्टि के लिए कोई वर्तमान समीक्षा नहीं है। पहले ऑर्डर चुनें।", "ಒಪ್ಪಲು ಈಗ ಪರಿಶೀಲನೆ ಇಲ್ಲ. ಮೊದಲು ಆರ್ಡರ್ ಆಯ್ಕೆಮಾಡಿ."),
    "not_eligible": ("This order cannot be cancelled under the pharmacy's current terms. No cancellation or refund was performed.", "फ़ार्मेसी की मौजूदा शर्तों के अनुसार यह ऑर्डर रद्द नहीं हो सकता। कोई रद्दीकरण या रिफंड नहीं किया गया।", "ಔಷಧಾಲಯದ ಈಗಿನ ಷರತ್ತುಗಳ ಪ್ರಕಾರ ಈ ಆರ್ಡರ್ ರದ್ದು ಮಾಡಲು ಸಾಧ್ಯವಿಲ್ಲ. ರದ್ದತಿ ಅಥವಾ ಮರುಪಾವತಿ ಮಾಡಲಾಗಿಲ್ಲ."),
    "back": ("Review closed. No new action was approved.", "समीक्षा बंद हुई। किसी नई कार्रवाई की मंज़ूरी नहीं दी गई।", "ಪರಿಶೀಲನೆ ಮುಚ್ಚಲಾಗಿದೆ. ಹೊಸ ಕ್ರಮಕ್ಕೆ ಒಪ್ಪಿಗೆ ನೀಡಿಲ್ಲ."),
    "uncertain": ("The result is not verified yet. Check status; QuickFix will reconcile the existing operation before retrying.", "परिणाम अभी सत्यापित नहीं है। स्थिति देखें; दोबारा कोशिश से पहले QuickFix मौजूदा कार्रवाई जाँचेगा।", "ಫಲಿತಾಂಶ ಇನ್ನೂ ಪರಿಶೀಲಿಸಲಾಗಿಲ್ಲ. ಸ್ಥಿತಿ ಪರಿಶೀಲಿಸಿ; ಮತ್ತೆ ಪ್ರಯತ್ನಿಸುವ ಮೊದಲು QuickFix ಈಗಿನ ಕಾರ್ಯವನ್ನು ಪರಿಶೀಲಿಸುತ್ತದೆ."),
    "invalid_action": ("That selection could not be verified. Open the current review and use its buttons, or reply ‘confirm’ to approve it.", "चयन सत्यापित नहीं हुआ। वर्तमान समीक्षा के बटन इस्तेमाल करें, या मंज़ूरी के लिए ‘पुष्टि करें’ लिखें।", "ಆಯ್ಕೆ ಪರಿಶೀಲಿಸಲಾಗಲಿಲ್ಲ. ಈಗಿನ ಪರಿಶೀಲನೆಯ ಬಟನ್ ಬಳಸಿ ಅಥವಾ ಒಪ್ಪಲು ‘ದೃಢೀಕರಿಸಿ’ ಎಂದು ಬರೆಯಿರಿ."),
    "access_required": ("Connect your authorized customer account before viewing orders.", "ऑर्डर देखने से पहले अपना अधिकृत ग्राहक खाता जोड़ें।", "ಆರ್ಡರ್ ನೋಡುವ ಮೊದಲು ನಿಮ್ಮ ಅಧಿಕೃತ ಗ್ರಾಹಕ ಖಾತೆಯನ್ನು ಜೋಡಿಸಿ."),
    "connection_required": ("Merchant connection required. The pharmacy's authorized order service and payment account are not ready. I cannot retrieve real orders or issue refunds yet. No sample orders are shown and no action has been performed.", "व्यापारी से कनेक्शन ज़रूरी है। फ़ार्मेसी की अधिकृत ऑर्डर सेवा और भुगतान खाता अभी तैयार नहीं हैं। अभी असली ऑर्डर नहीं देख सकता और रिफंड नहीं कर सकता। कोई नमूना ऑर्डर नहीं दिखाया गया और कोई कार्रवाई नहीं हुई।", "ವ್ಯಾಪಾರಿಯ ಸಂಪರ್ಕ ಅಗತ್ಯವಿದೆ. ಔಷಧಾಲಯದ ಅಧಿಕೃತ ಆರ್ಡರ್ ಸೇವೆ ಮತ್ತು ಪಾವತಿ ಖಾತೆ ಇನ್ನೂ ಸಿದ್ಧವಾಗಿಲ್ಲ. ಈಗ ನಿಜವಾದ ಆರ್ಡರ್ ಪಡೆಯಲು ಅಥವಾ ಮರುಪಾವತಿ ಮಾಡಲು ಸಾಧ್ಯವಿಲ್ಲ. ಯಾವುದೇ ಮಾದರಿ ಆರ್ಡರ್ ತೋರಿಸಿಲ್ಲ ಮತ್ತು ಯಾವುದೇ ಕ್ರಮ ಕೈಗೊಂಡಿಲ್ಲ."),
    "refunds_unavailable": ("These are the connected pharmacy's order details. Cancellation and refunds are unavailable until authorized merchant payment access and live refunds are enabled. No cancellation or refund was performed.", "ये जुड़ी फ़ार्मेसी के ऑर्डर का विवरण हैं। अधिकृत व्यापारी भुगतान पहुँच और लाइव रिफंड चालू होने तक रद्दीकरण और रिफंड उपलब्ध नहीं हैं। कोई रद्दीकरण या रिफंड नहीं हुआ।", "ಇವು ಸಂಪರ್ಕಿತ ಔಷಧಾಲಯದ ಆರ್ಡರ್ ವಿವರಗಳು. ಅಧಿಕೃತ ವ್ಯಾಪಾರಿ ಪಾವತಿ ಪ್ರವೇಶ ಮತ್ತು ನೇರ ಮರುಪಾವತಿ ಸಕ್ರಿಯಗೊಳ್ಳುವವರೆಗೆ ರದ್ದತಿ ಮತ್ತು ಮರುಪಾವತಿ ಲಭ್ಯವಿಲ್ಲ. ಯಾವುದೇ ರದ್ದತಿ ಅಥವಾ ಮರುಪಾವತಿ ನಡೆದಿಲ್ಲ."),
    "readonly_help": ("Say ‘show my orders’ to view your connected pharmacy's order details. Cancellation and refunds are currently disabled.", "जुड़ी फ़ार्मेसी के अपने ऑर्डर का विवरण देखने के लिए ‘मेरे ऑर्डर दिखाओ’ कहें। रद्दीकरण और रिफंड अभी बंद हैं।", "ಸಂಪರ್ಕಿತ ಔಷಧಾಲಯದ ನಿಮ್ಮ ಆರ್ಡರ್ ವಿವರಗಳನ್ನು ನೋಡಲು ‘ನನ್ನ ಆರ್ಡರ್ ತೋರಿಸಿ’ ಎಂದು ಹೇಳಿ. ರದ್ದತಿ ಮತ್ತು ಮರುಪಾವತಿ ಪ್ರಸ್ತುತ ನಿಷ್ಕ್ರಿಯವಾಗಿವೆ."),
    "no_case": ("Choose an order first so I can check its case.", "मामले की स्थिति जाँचने के लिए पहले ऑर्डर चुनें।", "ಪ್ರಕರಣ ಪರಿಶೀಲಿಸಲು ಮೊದಲು ಆರ್ಡರ್ ಆಯ್ಕೆಮಾಡಿ."),
    "language_changed": ("I will reply in English.", "मैं हिंदी में जवाब दूँगा।", "ನಾನು ಕನ್ನಡದಲ್ಲಿ ಉತ್ತರಿಸುತ್ತೇನೆ."),
    "tool_failed": ("A connected service could not complete the request. Check the case status before trying again.", "जुड़ी सेवा अनुरोध पूरा नहीं कर सकी। दोबारा कोशिश से पहले मामले की स्थिति देखें।", "ಸಂಪರ್ಕಿತ ಸೇವೆ ಕೋರಿಕೆಯನ್ನು ಪೂರ್ಣಗೊಳಿಸಲಿಲ್ಲ. ಮತ್ತೆ ಪ್ರಯತ್ನಿಸುವ ಮೊದಲು ಪ್ರಕರಣದ ಸ್ಥಿತಿ ಪರಿಶೀಲಿಸಿ."),
    "stale": ("The review changed or expired. Choose the order again to review its current details.", "समीक्षा बदल गई है या उसकी समय-सीमा पूरी हो गई। मौजूदा जानकारी के लिए ऑर्डर फिर चुनें।", "ಪರಿಶೀಲನೆ ಬದಲಾಗಿದೆ ಅಥವಾ ಗಡುವು ಮುಗಿದಿದೆ. ಈಗಿನ ವಿವರಗಳಿಗೆ ಆರ್ಡರ್ ಮತ್ತೆ ಆಯ್ಕೆಮಾಡಿ."),
    "blocked": ("This case needs verification before another action can proceed. Check its status.", "अगली कार्रवाई से पहले इस मामले का सत्यापन ज़रूरी है। इसकी स्थिति देखें।", "ಮುಂದಿನ ಕ್ರಮಕ್ಕೆ ಮೊದಲು ಈ ಪ್ರಕರಣದ ಪರಿಶೀಲನೆ ಅಗತ್ಯವಿದೆ. ಅದರ ಸ್ಥಿತಿ ಪರಿಶೀಲಿಸಿ."),
    "no_orders": ("There are no orders available for this account.", "इस खाते के लिए कोई ऑर्डर उपलब्ध नहीं है।", "ಈ ಖಾತೆಗೆ ಯಾವುದೇ ಆರ್ಡರ್ ಲಭ್ಯವಿಲ್ಲ."),
}

for _alias, _original in {"ineligible": "not_eligible", "auth_required": "access_required", "pending": "uncertain", "cancelled_draft": "back", "select_first": "no_case"}.items():
    _NOTICES[_alias] = _NOTICES[_original]

_MODES = {
    "gateway_simulated": ("Payment: local simulation; no gateway charge or refund.", "भुगतान: स्थानीय सिमुलेशन; गेटवे पर कोई चार्ज या रिफंड नहीं।", "ಪಾವತಿ: ಸ್ಥಳೀಯ ಅನುಕರಣೆ; ಗೇಟ್‌ವೇಯಲ್ಲಿ ಶುಲ್ಕ ಅಥವಾ ಮರುಪಾವತಿ ಇಲ್ಲ."),
    "gateway_razorpay_test": ("Payment: Razorpay test mode; no real money moves.", "भुगतान: Razorpay परीक्षण मोड; असली पैसे नहीं चलते।", "ಪಾವತಿ: Razorpay ಪರೀಕ್ಷಾ ಮೋಡ್; ನಿಜವಾದ ಹಣ ಚಲಿಸುವುದಿಲ್ಲ."),
    "gateway_unconfigured": ("Payment: merchant connection unavailable; refunds cannot be issued.", "भुगतान: व्यापारी कनेक्शन उपलब्ध नहीं; रिफंड नहीं किया जा सकता।", "ಪಾವತಿ: ವ್ಯಾಪಾರಿ ಸಂಪರ್ಕ ಲಭ್ಯವಿಲ್ಲ; ಮರುಪಾವತಿ ಮಾಡಲು ಸಾಧ್ಯವಿಲ್ಲ."),
    "gateway_razorpay_live": ("Payment: Razorpay live account; actions use actual merchant payment records.", "भुगतान: Razorpay लाइव खाता; कार्रवाइयों में व्यापारी के वास्तविक भुगतान रिकॉर्ड उपयोग होते हैं।", "ಪಾವತಿ: Razorpay ನೇರ ಖಾತೆ; ಕ್ರಮಗಳು ನಿಜವಾದ ವ್ಯಾಪಾರಿ ಪಾವತಿ ದಾಖಲೆಗಳನ್ನು ಬಳಸುತ್ತವೆ."),
    "gateway_live_readonly": ("Payment: Razorpay live account; refunds are disabled.", "भुगतान: Razorpay लाइव खाता; रिफंड बंद हैं।", "ಪಾವತಿ: Razorpay ನೇರ ಖಾತೆ; ಮರುಪಾವತಿಗಳು ನಿಷ್ಕ್ರಿಯವಾಗಿವೆ."),
    "pharmacy_unconfigured": ("Pharmacy: authorized connection pending.", "फ़ार्मेसी: अधिकृत कनेक्शन लंबित है।", "ಔಷಧಾಲಯ: ಅಧಿಕೃತ ಸಂಪರ್ಕ ಬಾಕಿ ಇದೆ."),
    "mail_unconfigured": ("Email: authorized connection pending; no email has been sent.", "ईमेल: अधिकृत कनेक्शन लंबित है; कोई ईमेल नहीं भेजा गया।", "ಇಮೇಲ್: ಅಧಿಕೃತ ಸಂಪರ್ಕ ಬಾಕಿ ಇದೆ; ಯಾವುದೇ ಇಮೇಲ್ ಕಳುಹಿಸಿಲ್ಲ."),
    "pharmacy_simulated": ("Pharmacy: local simulator, not the teammate's connected agent.", "फ़ार्मेसी: स्थानीय सिम्युलेटर; साथी का एजेंट जुड़ा नहीं है।", "ಔಷಧಾಲಯ: ಸ್ಥಳೀಯ ಅನುಕರಣೆ; ಸಹತಂಡದ ಏಜೆಂಟ್ ಸಂಪರ್ಕಿತವಾಗಿಲ್ಲ."),
    "pharmacy_connected": ("Pharmacy: connected service.", "फ़ार्मेसी: जुड़ी सेवा।", "ಔಷಧಾಲಯ: ಸಂಪರ್ಕಿತ ಸೇವೆ."),
    "mail_captured": ("Email: local capture; no external delivery.", "ईमेल: स्थानीय कैप्चर; बाहर डिलीवरी नहीं होती।", "ಇಮೇಲ್: ಸ್ಥಳೀಯ ದಾಖಲೆ; ಹೊರಗೆ ತಲುಪಿಸುವುದಿಲ್ಲ."),
    "mail_connected": ("Email: connected service; check each receipt's status below.", "ईमेल: जुड़ी सेवा; प्रत्येक रसीद की स्थिति देखें।", "ಇಮೇಲ್: ಸಂಪರ್ಕಿತ ಸೇವೆ; ಪ್ರತಿ ರಸೀದಿಯ ಸ್ಥಿತಿ ಪರಿಶೀಲಿಸಿ."),
}

_TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,200}\Z")
_APPROVE = {"approve", "confirm", "पुष्टि", "पुष्टि करें", "ದೃಢೀಕರಿಸಿ"}
_BACK = {"back", "go back", "never mind", "nevermind", "वापस", "वापस जाएँ", "ಹಿಂದೆ", "ಹಿಂದೆ ಹೋಗಿ"}


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().casefold()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate action field")
        result[key] = value
    return result


def parse_action(text: str) -> dict[str, Any] | None:
    """Accept bounded card actions or exact contextual approval, never prose/LLMs."""
    if not isinstance(text, str) or len(text) > 2500:
        return None
    clean = text.strip()
    if clean.startswith("{"):
        try:
            value = json.loads(clean, object_pairs_hook=_unique_object)
        except (ValueError, TypeError, RecursionError):
            return None
        if not isinstance(value, dict) or set(value) - {"action", "token", "approved"}:
            return None
        action = value.get("action")
        if not isinstance(action, str) or action not in {"qf_select", "qf_approve", "qf_back", "qf_status"}:
            return None
        token = value.get("token")
        if "token" in value and (not isinstance(token, str) or not _TOKEN.fullmatch(token)):
            return None
        if action in {"qf_select", "qf_approve"} and token is None:
            return None
        if "approved" in value:
            approved = value["approved"]
            # ASI adds approved=true to carousel/detail CTAs and false to
            # review rejection. The action remains authoritative: selecting,
            # reading status or going back never approves a financial write.
            if type(approved) is not bool or (action != "qf_back" and not approved):
                return None
        return {"action": action, **({"token": token} if token is not None else {})}
    natural = _normalized(clean).rstrip(".!").strip()
    if natural in _APPROVE:
        return {"action": "qf_approve"}
    if natural in _BACK:
        return {"action": "qf_back"}
    return None


def classify_text(text: str) -> dict[str, Any]:
    """Read-only intent and language; this function never authorizes a write."""
    if not isinstance(text, str) or len(text) > 2500:
        return {"intent": "unknown", "language": None}
    value = _normalized(text)
    language, switch = _language(value)
    if switch:
        intent = "language"
    elif value.rstrip(".!").strip() in _BACK:
        intent = "back"
    elif re.search(r"\b(?:status|update|track|progress)\b|स्थिति|स्टेटस|ಸ್ಥಿತಿ", value):
        intent = "status"
    elif re.search(r"\b(?:orders?|cancel|refund|purchase)\b|ऑर्डर|आर्डर|रद्द|रिफंड|ಆರ್ಡರ್|ರದ್ದು|ಮರುಪಾವತಿ", value):
        intent = "orders"
    elif re.fullmatch(r"(?:help|hello|hi|hey|start|menu|नमस्ते|मदद|ನಮಸ್ಕಾರ|ಸಹಾಯ)[.!?]*", value):
        intent = "help"
    else:
        intent = "unknown"
    return {"intent": intent, "language": language}


def _state(value: Any, language: str) -> str:
    key = str(value or "").lower()
    values = _STATES.get(key)
    return values[("en", "hi", "kn").index(language)] if values else _LABELS[language]["unknown"]


def _amount(view: dict[str, Any]) -> str:
    amount = view.get("amount_minor")
    currency = view.get("currency", "INR")
    if type(amount) is not int or amount < 0 or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        return "—"
    # The workflow currently supports INR and USD; other conventional precision
    # values are handled explicitly rather than rounding through floating point.
    precision = 0 if currency in {"JPY", "KRW"} else 3 if currency in {"BHD", "KWD", "OMR"} else 2
    unit = 10 ** precision
    number = str(amount) if not precision else f"{amount // unit:,}.{amount % unit:0{precision}d}"
    return f"{currency} {number}"


def _card(kind: str, payload: dict[str, Any]) -> MetadataContent:
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 64_000:
        raise ValueError("Card exceeds the protocol payload limit")
    return MetadataContent(metadata={"card_protocol_version": "1", "requires_card_interaction": "true", "card_kind": kind, "card_payload": encoded})


def _selection(action: str, token: Any = None) -> dict[str, str]:
    if token is not None and (not isinstance(token, str) or not _TOKEN.fullmatch(token)):
        raise ValueError("Invalid opaque card reference")
    if action in {"qf_select", "qf_approve"} and token is None:
        raise ValueError("Selection requires an opaque reference")
    return {"action": action, **({"token": token} if token is not None else {})}


def render_view(view: dict[str, Any], language: str = "en") -> tuple[str, MetadataContent | None]:
    """Render the engine's validated view without deriving execution outcomes."""
    language = language if language in _LABELS else "en"
    labels = _LABELS[language]
    index = ("en", "hi", "kn").index(language)
    gateway_mode = view.get("gateway_mode", "simulated")
    gateway = {"simulated": "gateway_simulated", "razorpay_test": "gateway_razorpay_test",
               "unconfigured": "gateway_unconfigured", "razorpay_live": "gateway_razorpay_live"}.get(gateway_mode, "gateway_unconfigured")
    read_only = gateway_mode == "unconfigured" or (gateway_mode == "razorpay_live" and not view.get("refunds_enabled", False))
    if gateway_mode == "razorpay_live" and read_only:
        gateway = "gateway_live_readonly"
    pharmacy = "pharmacy_connected" if view.get("pharmacy_mode") == "connected" else "pharmacy_simulated"
    mail = "mail_connected" if view.get("mail_mode") == "connected" else "mail_captured"
    if view.get("code") == "connection_required":
        pharmacy, mail = "pharmacy_unconfigured", "mail_unconfigured"
    prefix = "\n".join(_MODES[key][index] for key in (gateway, pharmacy, mail))
    if view.get("integration_note"):
        prefix += "\n" + str(view["integration_note"])
    kind = view.get("kind", "help")
    if kind == "help":
        help_text = _NOTICES["readonly_help"][index] if read_only else labels["help"]
        return prefix + "\n\n" + help_text, None
    if kind == "orders":
        orders = view.get("orders", [])
        if not orders:
            return prefix + "\n\n" + labels["empty"], None
        if len(orders) > 20:
            raise ValueError("Show at most twenty order choices per view")
        items, lines = [], []
        for order in orders:
            title = str(order["title"])
            pharmacy = str(order.get("pharmacy", ""))
            state = _state(order.get("order_state"), language)
            amount = _amount(order)
            selection = _selection("qf_select", order.get("token"))
            items.append({"id": selection["token"], "title": title, "subtitle": pharmacy,
                          "badges": [{"label": state, "variant": "info"}], "secondary_text": amount,
                          "primary_cta": {"label": labels["view_order" if read_only else "select"], "selection": selection}})
            lines.append(f"• {title} · {pharmacy} · {amount} · {state}")
        text = prefix + "\n\n" + labels["orders_hint"] + "\n" + "\n".join(lines)
        return text, _card("carousel", {"title": labels["orders"], "subtitle": labels["orders_hint"], "items": items})
    if kind == "order":
        rows = [{"label": labels["item"], "value": str(view.get("title", ""))},
                {"label": labels["pharmacy"], "value": str(view.get("pharmacy", ""))},
                {"label": labels["order_total"], "value": _amount(view)},
                {"label": labels["order_state"], "value": _state(view.get("order_state"), language)}]
        for item in view.get("items", []):
            rows.append({"label": labels["item"], "value": f"{item['name']} · {item['strength']} · {item['form']} · ×{item['quantity']}"})
        hint = _NOTICES["refunds_unavailable"][index]
        card = _card("detail", {"title": labels["order_detail"], "summary_rows": rows,
                               "ctas": [{"label": labels["check"], "selection": _selection("qf_status", view.get("token"))},
                                        {"label": labels["back"], "selection": _selection("qf_back")}]})
        return prefix + "\n\n" + labels["order_detail"] + "\n" + "\n".join(f"{row['label']}: {row['value']}" for row in rows) + "\n\n" + hint, card
    if kind in {"review", "status"}:
        rows = [{"label": labels["item"], "value": str(view.get("title", ""))},
                {"label": labels["pharmacy"], "value": str(view.get("pharmacy", ""))},
                {"label": labels["amount"], "value": _amount(view)}]
        for item in view.get("items", []):
            rows.append({"label": labels["item"], "value": f"{item['name']} · {item['strength']} · {item['form']} · ×{item['quantity']}"})
        if kind == "review":
            rows.append({"label": labels["destination"], "value": labels["original"]})
            for key, label in (("policy_text", "terms"), ("expires_at", "expires"), ("customer_email", "customer_email"), ("pharmacy_email", "pharmacy_email")):
                if view.get(key):
                    rows.append({"label": labels[label], "value": str(view[key])})
            payload = {"title": labels["review"], "summary_rows": rows,
                       "approve_cta": {"label": labels["approve"], "primary": True, "selection": _selection("qf_approve", view.get("token"))},
                       "reject_cta": {"label": labels["back"], "selection": _selection("qf_back", view.get("token"))}}
            hint = labels["review_hint"]
            card = _card("review", payload)
        else:
            if view.get("stage"):
                rows.append({"label": labels["stage"], "value": _state(view["stage"], language)})
            for key, label in (("order_state", "order_state"), ("payment_state", "payment_state"), ("refund_state", "refund_state"),
                               ("customer_notification", "customer_email"), ("pharmacy_notification", "pharmacy_email")):
                rows.append({"label": labels[label], "value": _state(view.get(key), language)})
            hint = labels["status_hint"]
            if view.get("last_error"):
                hint += "\n" + _NOTICES["uncertain"][index]
            if "captured" in {view.get("customer_notification"), view.get("pharmacy_notification")}:
                hint += "\n" + labels["captured_hint"]
            card = _card("detail", {"title": labels["status"], "summary_rows": rows,
                                    "ctas": [{"label": labels["check"], "primary": True, "selection": _selection("qf_status", view.get("token"))},
                                             {"label": labels["back"], "selection": _selection("qf_back")} ]})
        text = prefix + "\n\n" + labels[kind] + "\n" + "\n".join(f"{row['label']}: {row['value']}" for row in rows) + "\n\n" + hint
        return text, card
    if kind == "notice":
        values = _NOTICES.get(view.get("code"))
        message = values[("en", "hi", "kn").index(language)] if values else str(view.get("message") or labels["unknown"])
        return prefix + "\n\n" + message, None
    raise ValueError("Unknown commerce view kind")
