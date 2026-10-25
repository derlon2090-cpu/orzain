"""Check that every persisted paper ledger transaction balances."""
import argparse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
from app.db import SessionLocal
from app.paper_store import PaperLedgerStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("account_id")
    args = parser.parse_args()
    result = PaperLedgerStore(SessionLocal).verify_ledger(args.account_id)
    print(f"balanced={result['balanced']} transactions={result['transaction_count']} unbalanced={len(result['unbalanced_transaction_ids'])}")
    if not result["balanced"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
