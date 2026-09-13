import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from leaveops.integration import IntegrationWorker, MockBitrix24
from leaveops.service import LeaveOps, WorkflowError

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.app_path = directory / 'leaveops.db'
        self.external_path = directory / 'mock-bitrix24.db'
        self.app = LeaveOps(self.app_path)
        self.app.seed(ROOT / 'data/demo.json')

    def approve(self):
        row = self.app.create('analyst-b', '2026-10-15', '2026-10-16')
        self.app.transition('analyst-b', row['id'], 'submitted')
        return self.app.transition('manager', row['id'], 'approved')

    def test_approval_enqueues_event_in_same_transaction(self):
        row = self.approve()
        self.assertEqual([(e['operation'], e['status'], e['attempts']) for e in row['integration']],
                         [('upsert', 'pending', 0)])

    def test_blocked_approval_does_not_enqueue(self):
        row = self.app.create('analyst-b', '2026-10-14', '2026-10-16')
        self.app.transition('analyst-b', row['id'], 'submitted')
        with self.assertRaises(WorkflowError):
            self.app.transition('manager', row['id'], 'approved')
        self.assertEqual(self.app.get('manager', row['id'])['integration'], [])

    def test_delivery_is_idempotent_after_lost_response(self):
        row = self.approve()
        event_id = row['integration'][0]['id']
        adapter = MockBitrix24(self.external_path, lose_response_once_for={event_id})
        worker = IntegrationWorker(self.app_path, adapter)
        first = worker.run_once()
        self.assertEqual(first['status'], 'pending')
        external_before = adapter.list_absences()
        self.assertEqual(len(external_before), 1)
        second = worker.run_once()
        self.assertEqual(second['status'], 'delivered')
        external_after = adapter.list_absences()
        self.assertEqual(external_before, external_after)
        event = self.app.get('manager', row['id'])['integration'][0]
        self.assertEqual((event['status'], event['attempts'], event['last_error']), ('delivered', 2, None))

    def test_cancel_is_delivered_as_separate_event(self):
        row = self.approve()
        adapter = MockBitrix24(self.external_path)
        worker = IntegrationWorker(self.app_path, adapter)
        self.assertEqual(len(worker.drain()), 1)
        self.app.transition('analyst-b', row['id'], 'cancelled', 'Планы изменились')
        self.assertEqual(len(worker.drain()), 1)
        external = adapter.list_absences()
        self.assertEqual((len(external), external[0]['active']), (1, 0))
        events = self.app.get('manager', row['id'])['integration']
        self.assertEqual([(e['operation'], e['status']) for e in events],
                          [('upsert', 'delivered'), ('cancel', 'delivered')])

    def test_delivered_absence_is_rescheduled_through_update(self):
        row = self.approve()
        adapter = MockBitrix24(self.external_path)
        worker = IntegrationWorker(self.app_path, adapter)
        original = worker.drain()[0]['external_id']
        self.app.propose_reschedule('analyst-b', row['id'], '2026-10-20', '2026-10-21')
        changed = self.app.decide_reschedule('manager', row['id'], 'approved')
        self.assertEqual([(event['operation'], event['status']) for event in changed['integration']],
                         [('upsert', 'delivered'), ('update', 'pending')])
        self.assertEqual(worker.drain()[0]['external_id'], original)
        external = adapter.list_absences()
        self.assertEqual((len(external), external[0]['external_id'], external[0]['start_date'], external[0]['end_date'], external[0]['active']),
                         (1, original, '2026-10-20', '2026-10-21', 1))

    def test_update_retries_after_lost_response_without_new_external_absence(self):
        row = self.approve()
        adapter = MockBitrix24(self.external_path)
        worker = IntegrationWorker(self.app_path, adapter)
        original = worker.drain()[0]['external_id']
        self.app.propose_reschedule('analyst-b', row['id'], '2026-10-20', '2026-10-21')
        changed = self.app.decide_reschedule('manager', row['id'], 'approved')
        update_id = changed['integration'][-1]['id']
        flaky = MockBitrix24(self.external_path, lose_response_once_for={update_id})
        retrying_worker = IntegrationWorker(self.app_path, flaky)
        self.assertEqual(retrying_worker.run_once()['status'], 'pending')
        self.assertEqual(retrying_worker.run_once()['status'], 'delivered')
        external = flaky.list_absences()
        self.assertEqual((len(external), external[0]['external_id'], external[0]['start_date']), (1, original, '2026-10-20'))

    def test_expired_lease_is_recovered(self):
        row = self.approve()
        with closing(sqlite3.connect(self.app_path)) as db:
            with db:
                db.execute("UPDATE integration_events SET status='processing',worker_token='dead',lease_until='2000-01-01T00:00:00.000Z'")
        result = IntegrationWorker(self.app_path, MockBitrix24(self.external_path)).run_once()
        self.assertEqual(result['status'], 'delivered')
        self.assertEqual(self.app.get('manager', row['id'])['integration'][0]['attempts'], 1)

    def test_concurrent_workers_claim_once(self):
        row = self.approve()
        adapter = MockBitrix24(self.external_path)
        workers = [IntegrationWorker(self.app_path, adapter), IntegrationWorker(self.app_path, adapter)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda worker: worker.run_once(), workers))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(adapter.list_absences()), 1)
        self.assertEqual(self.app.get('manager', row['id'])['integration'][0]['attempts'], 1)


if __name__ == '__main__':
    unittest.main()
