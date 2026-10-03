"""Standalone, bounded CVE sync command.

Run with: python -m riskGenie.services.cve_sync_cli --help
"""

import argparse
import json

from .cve_sync_lock import SupabaseLeaseLock
from .cve_sync_service import CVESyncService
from . import cve_embedding
from .nvd_client import NVDClient
from .supabase_client import get_supabase_admin_client


def build_parser():
    parser = argparse.ArgumentParser(description="Run a bounded CVE sync")
    parser.add_argument("--start", dest="start_date")
    parser.add_argument("--end", dest="end_date")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--max-embeddings", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backfill-embeddings", action="store_true")
    parser.add_argument("--lease-seconds", type=int, default=300)
    return parser


def _validate_args(parser, args):
    if bool(args.start_date) != bool(args.end_date):
        parser.error("--start and --end must be supplied together")
    if args.max_pages is not None and args.max_pages <= 0:
        parser.error("--max-pages must be positive")
    if args.max_embeddings < 0:
        parser.error("--max-embeddings must be zero or positive")
    if args.lease_seconds < 30:
        parser.error("--lease-seconds must be at least 30")
    if args.backfill_embeddings and (args.start_date or args.end_date):
        parser.error("embedding backfill does not accept an NVD query window")


def main(argv=None, client_factory=get_supabase_admin_client, nvd_factory=NVDClient):
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)

    plan = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "max_pages": args.max_pages,
        "max_embeddings": args.max_embeddings,
        "dry_run": args.dry_run,
        "backfill_embeddings": args.backfill_embeddings,
    }
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    supabase = client_factory()
    lock = SupabaseLeaseLock(
        supabase,
        lease_seconds=args.lease_seconds,
    )
    with lock:
        if args.backfill_embeddings:
            result = cve_embedding.main(
                supabase=supabase,
                max_embeddings=args.max_embeddings,
            )
            lock.assert_held()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("error_count", 0) == 0 else 1

        service = CVESyncService(
            nvd_factory(),
            lambda: supabase,
            execution_guard=lock.assert_held,
        )
        result = service.run(
            last_mod_start_date=args.start_date,
            last_mod_end_date=args.end_date,
            max_pages=args.max_pages,
            max_embeddings=args.max_embeddings,
        )

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
