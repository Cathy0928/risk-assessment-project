from types import SimpleNamespace
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from riskGenie.services import cve_sync_cli
from riskGenie.services.cve_sync_lock import (
    SupabaseLeaseLock,
    SyncLockUnavailable,
)


class FakeRpcQuery:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return SimpleNamespace(data=self._data)


class FakeLockSupabase:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        return FakeRpcQuery(self.responses.pop(0))


def test_dry_run_does_not_create_clients_or_write(capsys):
    def reject_client():
        raise AssertionError("dry-run must not create Supabase client")

    result = cve_sync_cli.main(
        [
            "--start", "2024-01-01T00:00:00Z",
            "--end", "2024-01-02T00:00:00Z",
            "--max-pages", "1",
            "--max-embeddings", "5",
            "--dry-run",
        ],
        client_factory=reject_client,
    )

    assert result == 0
    assert '"dry_run": true' in capsys.readouterr().out


def test_cli_rejects_half_of_explicit_window():
    with pytest.raises(SystemExit):
        cve_sync_cli.main(
            ["--start", "2024-01-01T00:00:00Z", "--max-embeddings", "5"]
        )


def test_lease_lock_rejects_concurrent_holder_without_starting_heartbeat():
    supabase = FakeLockSupabase([False])
    lock = SupabaseLeaseLock(supabase, lease_seconds=300)

    with pytest.raises(SyncLockUnavailable, match="Another"):
        lock.acquire()

    assert [name for name, _payload in supabase.calls] == [
        "acquire_cve_sync_lease"
    ]


def test_expired_lease_can_be_acquired_and_released():
    supabase = FakeLockSupabase([True, True])
    lock = SupabaseLeaseLock(supabase, lease_seconds=300)

    lock.acquire()
    lock.release()

    assert [name for name, _payload in supabase.calls] == [
        "acquire_cve_sync_lease",
        "release_cve_sync_lease",
    ]
