import asyncio
import json

import httpx
import pytest

from orderfix.intent import IntentParser


def parse(text, **kwargs):
    return asyncio.run(IntentParser(**kwargs).parse(text))


@pytest.mark.parametrize("text, action, reason, number", [
    ("Where is order #1001?", "lookup", None, "#1001"),
    ("Order status 1001", "lookup", None, "#1001"),
    ("return status #1001", "status", None, "#1001"),
    ("Return order 1001 because the headphones are damaged", "return", "damaged", "#1001"),
    ("मेरा ऑर्डर १००१ खराब है, वापस करना है", "return", "damaged", "#1001"),
    ("ऑर्डर 1001 की वापसी की स्थिति", "status", None, "#1001"),
    ("ಆರ್ಡರ್ ೧೦೦೧ ಹಾಳಾಗಿದೆ, ವಾಪಸ್ ಮಾಡಬೇಕು", "return", "damaged", "#1001"),
    ("ಆರ್ಡರ್ #1001 ರಿಟರ್ನ್ ಸ್ಥಿತಿ", "status", None, "#1001"),
    ("order 1001 galat item aaya hai wapas karna hai", "return", "wrong_item", "#1001"),
    ("size bada hai", "return", "size", None),
    ("I changed my mind", "return", "changed_mind", None),
    ("wrong_item", "return", "wrong_item", None),
    ("changed_mind", "return", "changed_mind", None),
    ("ಸೈಜ್ ಸರಿಯಿಲ್ಲ", "return", "size", None),
    ("ಇಷ್ಟವಿಲ್ಲ", "return", "changed_mind", None),
    ("help", "help", None, None),
    ("#1001", "lookup", None, "#1001"),
])
def test_offline_multilingual_intents(text, action, reason, number):
    intent = parse(text)
    assert (intent.action, intent.reason, intent.order_number) == (action, reason, number)


@pytest.mark.parametrize("text, language", [("en", "en"), ("hi", "hi"), ("kn", "kn"), ("English", "en"), ("हिंदी", "hi"), ("ಕನ್ನಡ", "kn"), ("reply in Kannada", "kn"), ("हिंदी में बात करो", "hi")])
def test_explicit_languages(text, language):
    result = parse(text)
    assert result.action == "language"
    assert result.language == language


def test_extract_explicit_item_and_quantity():
    result = parse("return #1001 item 2 quantity 3 wrong item")
    assert result.item_index == 2
    assert result.quantity == 3
    assert result.reason == "wrong_item"
    assert parse("return item 1 मात्रा २").quantity == 2
    assert parse("return ವಸ್ತು ೨ ಪ್ರಮಾಣ ೩").item_index == 2
    assert parse("1").item_index == 1


@pytest.mark.parametrize("text", ["return item 101", "return item 1001", "return quantity 999", "return qty 0", "return qty -1", "return item -2", "return 0 units", "101"])
def test_invalid_explicit_item_and_quantity_are_not_silently_defaulted(text):
    with pytest.raises(ValueError):
        parse(text)


@pytest.mark.parametrize("text", ["CONFIRM abc123", "CANCEL", "ACCESS test-secret", "approve return #1001", "verify customer", "confirm return damaged"])
def test_control_commands_are_not_model_authorization(text):
    assert parse(text).action == "unknown"


def test_long_input_is_bounded():
    assert parse("return " * 1000).action == "unknown"


def test_optional_provider_classifies_only_and_never_supplies_ids():
    async def run():
        async def handler(request):
            payload = json.loads(request.content)
            assert payload["model"] == "asi1"
            assert request.headers["authorization"] == "Bearer test-key"
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"action":"return","reason":"damaged"}'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await IntentParser(api_key="test-key", client=client).parse("It arrived in several pieces")
            assert result.action == "return"
            assert result.reason == "damaged"
            assert result.order_number is None
    asyncio.run(run())


@pytest.mark.parametrize("content", [
    '{"action":"confirm"}',
    '{"action":"return","order_number":"#9999"}',
    '{"action":"return","quantity":2}',
    'not json',
    '```json\n{"action":"return"}\n```',
    '[]',
])
def test_invalid_provider_response_falls_back(content):
    async def run():
        async def handler(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await IntentParser(api_key="test-key", client=client).parse("something unfamiliar")
            assert result.action == "unknown"
            assert result.order_number is None
    asyncio.run(run())


def test_provider_failure_is_recoverable_and_known_text_stays_offline():
    async def run():
        calls = []
        async def handler(request):
            calls.append(request)
            raise httpx.ConnectError("unavailable")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            parser = IntentParser(api_key="test-key", client=client)
            assert (await parser.parse("help")).action == "help"
            assert (await parser.parse("CONFIRM token")).action == "unknown"
            assert not calls
            assert (await parser.parse("unfamiliar request")).action == "unknown"
            assert len(calls) == 1
    asyncio.run(run())
