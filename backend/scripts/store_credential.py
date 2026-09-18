"""
One-time script to store an encrypted login credential in the DB.
Run once per application you want the discovery agent to crawl.

Usage:
    python scripts/store_credential.py \
        --ref "qa-agent-prod" \
        --username "nikita@gmail.com" \
        --password "yourpassword" \
        --login-url "https://qa-agent-4ym9.onrender.com/login" \
        --username-selector "email" \
        --password-selector "password" \
        --submit-selector "sign in"
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from infra.db.models.discovery_credential import DiscoveryCredential
from infra.db.session import AsyncSessionLocal
from infra.secrets.vault_client import encrypt_login_secret


async def store(args: argparse.Namespace) -> None:
    secret = {
        "username": args.username,
        "password": args.password,
        "username_selector": args.username_selector,
        "password_selector": args.password_selector,
        "submit_selector": args.submit_selector,
    }
    if args.login_url:
        secret["login_url"] = args.login_url

    encrypted = encrypt_login_secret(secret)

    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(DiscoveryCredential).where(
                    DiscoveryCredential.credential_ref == args.ref
                )
            )
        ).scalar_one_or_none()

        if existing:
            existing.encrypted_secret = encrypted
            print(f"[OK] Updated credential: {args.ref}")
        else:
            session.add(
                DiscoveryCredential(
                    credential_ref=args.ref,
                    encrypted_secret=encrypted,
                )
            )
            print(f"[OK] Stored new credential: {args.ref}")

        await session.commit()
        print("Done. Use this ref in your discovery payload:")
        print(f'  "credential_ref": "{args.ref}"')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Store an encrypted login credential")
    parser.add_argument("--ref", required=True, help="Key to use in discovery payload, e.g. 'qa-agent-prod'")
    parser.add_argument("--username", required=True, help="Login email / username")
    parser.add_argument("--password", required=True, help="Login password")
    parser.add_argument("--login-url", default=None, help="Full URL of the login page")
    parser.add_argument("--username-selector", default="email", help="Accessible name of the email field")
    parser.add_argument("--password-selector", default="password", help="Accessible name of the password field")
    parser.add_argument("--submit-selector", default="sign in", help="Accessible name of the submit button")
    args = parser.parse_args()
    asyncio.run(store(args))
