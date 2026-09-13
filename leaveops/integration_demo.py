"""Reproduce reliable delivery to the local Bitrix24 imitation."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .integration import IntegrationWorker, MockBitrix24
from .service import LeaveOps


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    with TemporaryDirectory() as directory:
        directory = Path(directory)
        application_db = directory / 'leaveops.db'
        external_db = directory / 'mock-bitrix24.db'
        app = LeaveOps(application_db)
        app.seed(Path(__file__).resolve().parents[1] / 'data/demo.json')
        request = app.create('analyst-b', '2026-10-15', '2026-10-16')
        app.transition('analyst-b', request['id'], 'submitted')
        approved = app.transition('manager', request['id'], 'approved')
        event_id = approved['integration'][0]['id']
        adapter = MockBitrix24(external_db, lose_response_once_for={event_id})
        worker = IntegrationWorker(application_db, adapter)
        first = worker.run_once()
        state_after_lost_response = adapter.list_absences()
        second = worker.run_once()
        state_after_retry = adapter.list_absences()
        assert first['status'] == 'pending'
        assert second['status'] == 'delivered'
        assert state_after_lost_response == state_after_retry
        app.transition('analyst-b', request['id'], 'cancelled', 'Демонстрационная отмена')
        worker.drain()
        final = adapter.list_absences()
        assert len(final) == 1 and final[0]['active'] == 0
        print(json.dumps({
            'first_attempt': first,
            'retry': second,
            'external_records_after_retry': len(state_after_retry),
            'duplicate_created': False,
            'active_after_cancel': bool(final[0]['active']),
            'events': app.get('manager', request['id'])['integration'],
        }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
