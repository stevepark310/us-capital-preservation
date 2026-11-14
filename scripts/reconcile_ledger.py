#!/usr/bin/env python3
"""
Ledger reconciliation — cross-checks the live account files and repairs only
structural flags (trading-day markers). Financial figures are never rewritten
automatically; they are reported for review.

Run weekly or after any settlement: python3 scripts/reconcile_ledger.py [--fix]
"""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import market_snapshot, ledger as ledger_mod


def main():
    fix = "--fix" in sys.argv
    snap = market_snapshot.build_snapshot(quiet=True)
    cash = ledger_mod.ensure_cash_fields()
    report = ledger_mod.validate(snapshot=snap, fix=fix)
    led = ledger_mod.summary(snapshot=snap)

    print(f"fx_rate {report['fx_rate']} ({report['fx_source']})")
    print(f"cash ${report['canonical_cash']['usd']:,.2f} / {report['canonical_cash']['krw']:,}원 "
          f"(연동 필드 {'갱신' if cash.get('changed') else '유지'})")
    print(f"equity ${led['equity_usd']:,.2f} / {led['equity_krw']:,}원 | realized +${led['realized_pnl_usd']} "
          f"| unrealized ${led['unrealized_pnl_usd']}")
    print(f"status {report['status']} | issues {report['issue_count']}")
    for i in report["issues"]:
        print(f"  [{i['severity']}] {i['code']}: {i['detail']}")
    if report.get("fixed_days"):
        print(f"  거래일 플래그 수정: {len(report['fixed_days'])}일")
    print("saved data/ledger_consistency.json")
    return 0 if report["issue_count"] == 0 or fix else 1


if __name__ == "__main__":
    sys.exit(main())
