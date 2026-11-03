"""Lifecycle scheduling for the single guarded CRM analysis worker."""
from __future__ import annotations
import asyncio
import time


class AnalysisMaintenance:
    def __init__(self,people,factory):
        self.people,self.factory=people,factory
        self._lock=asyncio.Lock()

    def retry(self,pid):
        service=self.factory();service._require_schema();service._prepare(pid)
        with self.people.crm_transaction() as conn:
            conn.execute("UPDATE crm_analysis_state SET attempts=0,next_retry_at=NULL,failure_code=NULL WHERE person_id=?",(pid,))

    def recover_interrupted(self):
        self._lock=asyncio.Lock()
        self.people.require_crm_schema()
        with self.people.crm_transaction() as conn:
            conn.execute("UPDATE crm_analysis_attempts SET outcome='interrupted',failure_code='interrupted',updated_at=? WHERE outcome='running'",(self.people._now(),))
            conn.execute("UPDATE crm_analysis_state SET lease_token=NULL,lease_until=NULL,outcome='pending' WHERE lease_token IS NOT NULL")

    def retry_delay(self):
        with self.people._lock:
            row=self.people._conn.execute('SELECT min(next_retry_at) FROM crm_analysis_state WHERE latest_revision!=analyzed_revision AND attempts<3').fetchone()
        return max(1.,min(300.,row[0]-time.time())) if row and row[0] else 300.

    async def run_pending(self):
        async with self._lock:
            service=self.factory();service._require_schema()
            with self.people._lock:
                changed=[r[0] for r in self.people._conn.execute('SELECT s.person_id FROM crm_analysis_state s JOIN people p ON p.person_id=s.person_id WHERE p.deleted_at IS NULL AND latest_evidence_revision>observed_collection_revision ORDER BY s.updated_at LIMIT 100')]
            for pid in changed:
                service._prepare(pid)
            for pid in service.pending():
                await self.factory().analyze(pid)


class RefreshCoordinator:
    """Share overlapping refresh requests; one lifecycle-owned task does maintenance."""
    def __init__(self, collect, analysis, *, connected=lambda: True):
        self.collect, self.analysis, self.connected = collect, analysis, connected
        self._current = None
        self._collection = None
        self._wake = asyncio.Event()

    def request(self):
        if self._current is None or self._current.done():
            self._collection = asyncio.create_task(self._collect())
            self._current = asyncio.create_task(self._refresh(self._collection))
            self._current.add_done_callback(self._finished)
        return self._current

    def _finished(self, task):
        self._wake.set()
        if not task.cancelled():
            task.exception()  # Consume failures even if only collection was awaited.

    async def collection_result(self):
        self.request()
        return await asyncio.shield(self._collection)

    async def _collect(self):
        if self.connected():
            return await asyncio.to_thread(self.collect)
        return {"status": "failed", "scanned": 0, "filed": 0, "unassigned": 0}

    async def _refresh(self, collection):
        await collection
        await self.analysis.run_pending()

    async def run(self):
        self._wake=asyncio.Event()
        self.analysis.recover_interrupted()
        loop = asyncio.get_running_loop()
        next_collection = 0.0
        try:
            while True:
                self._wake.clear()
                try:
                    if loop.time() >= next_collection:
                        await asyncio.shield(self.request())
                        next_collection = loop.time() + 300
                    else:
                        await self.analysis.run_pending()
                except Exception:
                    next_collection = loop.time() + 300
                delay = min(max(1.0, next_collection - loop.time()), self.analysis.retry_delay())
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except TimeoutError:
                    pass
        finally:
            if self._current and not self._current.done():
                self._current.cancel()
                try:
                    await self._current
                except asyncio.CancelledError:
                    pass
