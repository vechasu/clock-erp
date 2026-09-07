"""Local synthetic repairs only; shared fixture server uses temporary databases."""
import os
import runpy
from pathlib import Path

state = runpy.run_path(str(Path(__file__).with_name('stage2_preview_server.py')))
web, root = state['web'], state['PREVIEW_ROOT']
web.get_repair_cases_path = lambda: root / 'repair_cases.json'
web.save_repair_cases([
    dict(id='ux-' + str(i), status=status, location=location, client_name='Тестовый клиент',
         contact='@test', product_name='LUNAR Chrome ' + ('Длинное название ' * 8 if i == 1 else ''),
         problem='Минутный диск сбивается при носке. ' * 8, control_date='2026-10-01',
         waiting_for='us', next_action='Проверить ремонт', history=[],
         archived_at='2026-09-01' if i == 7 else '')
    for i, (status, location) in enumerate([
        ('new', 'at_us'), ('waiting_diagnostics', 'at_us'), ('diagnostics', 'with_master'),
        ('waiting_decision', 'with_master'), ('ready_return', 'at_us'),
        ('new', 'delivered'), ('completed', 'delivered'), ('waiting_payment', 'outbound_transit'),
    ])
])
if __name__ == '__main__':
    web.app.run(host='127.0.0.1', port=int(os.environ.get('PREVIEW_PORT', '4187')), use_reloader=False)
