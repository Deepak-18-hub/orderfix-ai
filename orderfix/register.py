"""Preview or explicitly register the local OrderFix identity as a mailbox agent.

The seed stays on this machine. The SDK submits a signed ownership challenge,
public profile, and API-key authorization to Agentverse. No tunnel is needed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
import requests
from uagents_core.config import AgentverseConfig
from uagents_core.contrib.protocols.chat import chat_protocol_spec
from uagents_core.identity import Identity
from uagents_core.protocol import ProtocolSpecification
from uagents_core.utils.registration import (
    AgentverseRegistrationRequest,
    AgentverseRequestError,
    RegistrationRequestCredentials,
    register_agent,
)
from .profile import AVATAR_URL, DESCRIPTION, PUBLIC_HANDLE

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = PROJECT_ROOT / "AGENT_PROFILE.md"


def registration_request(readme: str) -> AgentverseRegistrationRequest:
    """Use the SDK's official ACP manifest, never a hard-coded protocol hash."""
    digest = ProtocolSpecification.compute_digest(chat_protocol_spec.manifest())
    return AgentverseRegistrationRequest(
        name="OrderFix", endpoint=AgentverseConfig().mailbox_endpoint,
        handle=PUBLIC_HANDLE,
        type="mailbox", protocols=[digest], active=True,
        # The SDK's default True replaces endpoints with the proxy endpoint.
        track_interactions=False, description=DESCRIPTION, readme=readme, avatar_url=AVATAR_URL,
        starter_prompts=["quickfix show my orders", "quickfix मेरे ऑर्डर दिखाओ",
                         "quickfix ನನ್ನ ಆರ್ಡರ್ ತೋರಿಸಿ"],
        metadata={"is_public": "True", "categories": ["shopping", "customer-support"],
                  "languages": ["English", "Hindi", "Kannada"]},
    )


@contextmanager
def _quiet_sdk():
    # A remote error body may contain arbitrary text. Only our fixed messages
    # should reach the console, even if a dependency has debug logging enabled.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def _failure_message(error: Exception) -> str:
    cause = error.from_exc if isinstance(error, AgentverseRequestError) else error
    if isinstance(cause, requests.HTTPError) and cause.response is not None:
        status = cause.response.status_code
        if status in (401, 403):
            return "Agentverse rejected access. Check the local AGENTVERSE_API_KEY and its account permissions."
        if status == 409:
            return "This agent identity conflicts with an existing registration. Check the owning Agentverse account. Keep the existing seed."
    if isinstance(cause, (requests.Timeout, requests.ConnectionError)):
        return "Agentverse could not be reached. Registration may be incomplete; check My Agents before retrying with the same seed."
    return "Agentverse did not confirm all registration steps. Check My Agents before retrying with the same seed; credentials and remote details were not printed."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--register", action="store_true", help="Create or update the public Agentverse profile and activate the mailbox identity")
    actions.add_argument("--dry-run", action="store_true", help="Preview only (the default); no network calls")
    args = parser.parse_args(argv)
    try:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        seed = os.getenv("AGENT_SEED", "")
        api_key = os.getenv("AGENTVERSE_API_KEY", "").strip()
        if len(seed) < 32:
            print("A stable AGENT_SEED is required. Run python -m orderfix.setup_local, then keep the generated .env private.")
            return 2
        readme = PROFILE_PATH.read_text(encoding="utf-8")
        if not readme.strip():
            print("AGENT_PROFILE.md is empty. Complete the public profile before registration.")
            return 2
        if os.getenv("STORE_MODE", "demo") != "demo" and "currently uses a synthetic demo store" in readme:
            print("Update AGENT_PROFILE.md to describe the current store mode before publishing its profile.")
            return 2
        request = registration_request(readme)
        address = Identity.from_seed(seed, 0).address
    except Exception:
        print("Registration configuration could not be read. Check .env and AGENT_PROFILE.md locally; no credential values were printed.")
        return 2

    print("OrderFix public registration review:")
    print(json.dumps({
        "name": request.name, "address": address, "type": request.type,
        "endpoint": request.endpoint, "protocols": request.protocols,
        "description": request.description, "profile_file": PROFILE_PATH.name,
        "profile_characters": len(readme), "languages": ["English", "Hindi", "Kannada"],
        "activate": request.active, "api_key_configured": bool(api_key),
    }, indent=2, ensure_ascii=True))
    if not args.register:
        print("Preview only: no network calls or registration changes. Review AGENT_PROFILE.md, then run python -m orderfix.register --register.")
        return 0
    if not api_key:
        print("AGENTVERSE_API_KEY is missing. Generate your own key in Agentverse and save it in the local .env, then rerun. Do not paste the key into chat.")
        return 2

    try:
        credentials = RegistrationRequestCredentials(agentverse_api_key=api_key, agent_seed_phrase=seed)
        # The SDK checks the existing address before creating ownership proof.
        # Existing registrations are updated under the caller's permissions.
        with _quiet_sdk():
            success = register_agent(request, AgentverseConfig(), credentials)
        if not success:
            print("Agentverse did not confirm registration. Check My Agents before retrying with the same seed.")
            return 2
    except Exception as error:
        print(_failure_message(error))
        return 2
    print("Agentverse accepted the mailbox registration and activation. An existing profile for this identity is updated rather than creating a new identity.")
    print("Open My Agents in Agentverse, verify the OrderFix address above, and test the conversation in ASI:One. Keep python -m orderfix.agent running; no public tunnel is required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
