"""Merchant-operated test access; no real-customer authentication is claimed."""
import argparse

from .config import Settings
from .storage import Storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("customer", help="Test customer GID: gid://shopify/Customer/...")
    args = parser.parse_args()
    storage = Storage(Settings.load().database)
    try:
        code = storage.create_invite(args.customer)
        print("Private test access code (one chat, valid for 2 hours):")
        print("access " + code)
        print("Do not publish a chat share containing this code while it is valid.")
    finally:
        storage.close()


if __name__ == "__main__":
    main()
