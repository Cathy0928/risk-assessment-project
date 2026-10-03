"""Database-backed renewable lease for cross-process CVE sync exclusion."""

import threading
import uuid


class SyncLockUnavailable(RuntimeError):
    pass


class SupabaseLeaseLock:
    def __init__(self, supabase, name="cve-knowledge-sync", lease_seconds=300):
        self._supabase = supabase
        self._name = name
        self._lease_seconds = lease_seconds
        self._token = str(uuid.uuid4())
        self._stop = threading.Event()
        self._thread = None
        self._lost = False

    def acquire(self):
        response = self._supabase.rpc(
            "acquire_cve_sync_lease",
            {
                "requested_lock_name": self._name,
                "requested_token": self._token,
                "requested_lease_seconds": self._lease_seconds,
            },
        ).execute()
        acquired = bool(response.data)
        if not acquired:
            raise SyncLockUnavailable("Another CVE sync process holds the lease")

        self._thread = threading.Thread(target=self._renew_loop, daemon=True)
        self._thread.start()
        return self

    def _renew_loop(self):
        interval = max(self._lease_seconds / 3, 1)
        while not self._stop.wait(interval):
            try:
                response = self._supabase.rpc(
                    "renew_cve_sync_lease",
                    {
                        "requested_lock_name": self._name,
                        "requested_token": self._token,
                        "requested_lease_seconds": self._lease_seconds,
                    },
                ).execute()
                if not bool(response.data):
                    self._lost = True
                    return
            except Exception:
                self._lost = True
                return

    def assert_held(self):
        if self._lost:
            raise SyncLockUnavailable("CVE sync lease was lost during execution")

    def release(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        try:
            self._supabase.rpc(
                "release_cve_sync_lease",
                {
                    "requested_lock_name": self._name,
                    "requested_token": self._token,
                },
            ).execute()
        finally:
            self._thread = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, _exc_type, _exc, _traceback):
        self.release()
