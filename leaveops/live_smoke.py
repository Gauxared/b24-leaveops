"""Create, verify and remove one synthetic event in a configured Bitrix24 calendar."""

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .integration import (IntegrationWorker, MappedBitrix24CalendarAdapter,
                          bitrix_adapter_from_environment)
from .service import LeaveOps


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    adapter = bitrix_adapter_from_environment()
    request_id = None
    payload = None
    external_id = None
    try:
        with TemporaryDirectory() as directory:
            application_db = Path(directory) / 'leaveops.db'
            app = LeaveOps(application_db)
            app.seed(Path(__file__).resolve().parents[1] / 'data/demo.json')
            request = app.create('analyst-b', '2099-01-15', '2099-01-16')
            request_id = request['id']
            app.transition('analyst-b', request_id, 'submitted')
            app.transition('manager', request_id, 'approved')
            worker = IntegrationWorker(application_db, adapter)
            created = worker.run_once()
            payload = {'request_id': request_id, 'employee_id': 'analyst-b',
                       'start_date': '2099-01-15', 'end_date': '2099-01-16'}
            verifier = (adapter._adapter(adapter.mappings['analyst-b'])
                        if isinstance(adapter, MappedBitrix24CalendarAdapter) else adapter)
            external_id = verifier._find(payload)
            assert created and created['status'] == 'delivered' and external_id
            app.transition('analyst-b', request_id, 'cancelled', 'Завершение smoke-теста')
            cancelled = worker.run_once()
            assert cancelled and cancelled['status'] == 'delivered'
            assert verifier._find(payload) is None
            print(json.dumps({'created': True, 'event_found_after_create': True,
                              'cancelled': True, 'event_found_after_cancel': False,
                              'owner_id': verifier.owner_id,
                              'section_id': verifier.section_id}, ensure_ascii=False, indent=2))
    finally:
        # Best-effort cleanup if an assertion or transport error interrupted the normal cancellation.
        if payload:
            verifier = (adapter._adapter(adapter.mappings['analyst-b'])
                        if isinstance(adapter, MappedBitrix24CalendarAdapter) else adapter)
            remaining = verifier._find(payload)
            if remaining:
                verifier._call('calendar.event.delete', {'id': int(remaining)})


if __name__ == '__main__':
    main()
