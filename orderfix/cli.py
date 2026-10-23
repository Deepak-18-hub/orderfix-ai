from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace
from uuid import uuid4

from .config import Settings, make_engine


async def run(args):
    settings = replace(Settings.load(), mode=args.mode, database=args.db)
    engine = make_engine(settings)
    print(f"LOCAL HARNESS | mode={settings.mode} | database={settings.database}")
    print("This terminal is not an ASI:One integration test. Type quit to exit.")
    session = args.session or str(uuid4())
    print(f"Session: {session} (reuse --session to resume)")

    async def turn(text, message_id=None):
        print("\nYou: " + text)
        reply = await engine.handle("local-customer", session, message_id or str(uuid4()), text)
        print("\nOrderFix: " + reply.text)
        return reply

    try:
        if args.scripted:
            if settings.mode != "demo":
                raise ValueError("Scripted auto-confirmation is restricted to synthetic local demo orders")
            await turn(args.language)
            await turn("order #1001")
            text = {"en": "return #1001 item 1 quantity 1 damaged", "hi": "ऑर्डर #1001 का आइटम 1 खराब है, वापस करना है",
                    "kn": "ಆರ್ಡರ್ #1001 ವಸ್ತು 1 ಹಾಳಾಗಿದೆ, ವಾಪಸ್ ಮಾಡಬೇಕು"}[args.language]
            review = await turn(text)
            if not review.review:
                raise RuntimeError("Expected review; see output")
            message_id = str(uuid4())
            confirmed = await turn("confirm " + review.review.token, message_id)
            if not confirmed.return_record:
                raise RuntimeError("Expected verified return; see output")
            duplicate = await turn("confirm " + review.review.token, message_id)
            assert duplicate.return_record.id == confirmed.return_record.id
            await turn("return status #1001")
            await turn("return #1002 item 1 damaged")
            print("\nVERIFIED: order lookup, policy check, confirmation, persisted return, duplicate replay, status lookup.")
        else:
            await turn(args.language)
            await turn("help")
            while True:
                text = input("\nYou> ").strip()
                if text.lower() in ("quit", "exit"):
                    break
                await turn(text)
    finally:
        engine.provider.close()
        engine.storage.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="OrderFix local conversation test harness")
    parser.add_argument("--mode", choices=["demo", "shopify"], default="demo")
    parser.add_argument("--db", default="data/local-demo.sqlite3")
    parser.add_argument("--language", choices=["en", "hi", "kn"], default="en")
    parser.add_argument("--session")
    parser.add_argument("--scripted", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
