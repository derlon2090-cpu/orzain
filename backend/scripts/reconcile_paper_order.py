"""Manually resolve a persisted paper command after verifying its durable state."""
import argparse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
from app.db import SessionLocal
from app.paper_store import PaperLedgerStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("order_id", help="UUID of the PENDING_RECONCILIATION paper order")
    args = parser.parse_args()
    result = PaperLedgerStore(SessionLocal).reconcile_order(args.order_id)
    print(f"{result['order_id']} {result['status']}")


if __name__ == "__main__":
    main()
