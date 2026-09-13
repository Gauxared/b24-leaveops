"""Run an isolated acceptance scenario without changing the user's database."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .service import LeaveOps, WorkflowError


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    with TemporaryDirectory() as directory:
        app = LeaveOps(Path(directory) / 'demo.db')
        app.seed(Path(__file__).resolve().parents[1] / 'data/demo.json')
        blocked = app.create('analyst-b', '2026-10-14', '2026-10-16')['id']
        app.transition('analyst-b', blocked, 'submitted')
        try:
            app.transition('manager', blocked, 'approved')
            raise AssertionError('Conflicting request was approved')
        except WorkflowError as error:
            assert error.code == 'conflict'
            conflicts = error.conflicts
        accepted = app.create('analyst-b', '2026-10-15', '2026-10-16')['id']
        app.transition('analyst-b', accepted, 'submitted')
        app.transition('manager', accepted, 'approved')
        app = LeaveOps(Path(directory) / 'demo.db')
        approved = app.get('manager', accepted)
        assert approved['status'] == 'approved'
        app.transition('analyst-b', accepted, 'cancelled', 'Демонстрационная отмена')
        print(json.dumps({
            'blocked_request_status': app.get('manager', blocked)['status'],
            'conflicts': conflicts,
            'approved_status_after_restart': approved['status'],
            'final_status': app.get('manager', accepted)['status'],
            'history': [h['to_status'] for h in app.get('manager', accepted)['history']],
        }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
