# Автоматическая синхронизация WB: проверка перед установкой

FAST: каждые 5 минут, /orders/new и статусы известных нетерминальных заказов пакетами по 100. Новая выдача /orders/new не заменяет существующие карточки; актуальные статусы берутся из /orders/status.
FULL: каждый час в :02, статусы всех известных заказов (включая терминальные для поздних изменений) и существующий recovery за 14 дней. Продажи и остатки сервис не проводит.

Терминальные wbStatus: sold, canceled, canceled_by_client, declined_by_client, defect. Неизвестные статусы продолжают опрашиваться. Основание: https://dev.wildberries.ru/en/docs/openapi/orders-fbs .

Ручная кнопка использует общий FULL orchestration. Оба timer, оба Gunicorn worker и ручной recovery/import используют flock рядом с базой orders.db. SQLite-транзакции не удерживаются во время API-запросов. При занятом lock CLI пишет WB_SYNC_RUNNING и завершается успешно без второго sync.

UPDATE существующей карточки разрешён только для supplier_status, wb_status, WB presentation status/name, synced_at, wb_status_updated_at, wb_status_metadata, двух status-полей wb_raw и добавления записи wb_status_history. Products, mapping, sale_id, comment, локальная history и остальные поля сохраняются. Одинаковые статусы вообще не переписывают карточку. Диагностика хранится в существующей orders_snapshot_meta, без миграций.

HTTP timeout 3.05/15 секунд, максимум 2 повтора временных ошибок с backoff. FAST ограничен 30 HTTP-попытками и бюджетом 200 секунд, FULL — 200 попытками и 840 секундами; CLI alarm 240/900 секунд, systemd timeout 270/930 секунд. Частичный результат сохраняет предыдущую дату успеха и не возвращается интерфейсу как успех.

## Проверки до deploy

- Целевые WB тесты: новые заказы, повторный sync, смена статуса, история, сохранность ERP-полей, отсутствие продаж/складских изменений, terminal polling, ошибки API, partial success, flock, одновременное чтение SQLite.
- Компиляция 8 production Python-файлов на Python 3.6.8: успешно.
- systemd-analyze verify на production systemd 219 для четырёх unit-файлов из временного каталога: exit 0, без предупреждений. RandomizedDelaySec не используется для совместимости с systemd 219.
- Offline benchmark: 100 активных заказов с неизменными статусами, 2 вызова fake WB, 101 SELECT, 2 INSERT диагностики, 0 UPDATE карточек; wall 0.0042 сек, CPU 4.1 мс, peak RSS 23.3 МиБ (macOS). Это не измерение реальной задержки WB. Для N активных заказов нормальная стоимость FAST: 1 + ceil(N/100) запросов без retry.
- Команда воспроизведения: python -m scripts.benchmark_wb_sync. Только временная база и fake транспорт.

## Unit-файлы до установки

### vechasu-wb-sync-full.service

```ini
[Unit]
Description=Vechasu WB full synchronization
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/clock-erp
EnvironmentFile=/etc/clock-erp/clock-erp.env
ExecStart=/opt/clock-erp/venv/bin/python -m scripts.wb_orders_sync --mode full
TimeoutStartSec=930
Nice=10
IOSchedulingClass=idle
UMask=0077
```

### vechasu-wb-sync-full.timer

```ini
[Unit]
Description=Vechasu WB full timer

[Timer]
OnCalendar=*-*-* *:02:00
Persistent=true
Unit=vechasu-wb-sync-full.service

[Install]
WantedBy=timers.target
```

### vechasu-wb-sync.service

```ini
[Unit]
Description=Vechasu WB fast synchronization
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/clock-erp
EnvironmentFile=/etc/clock-erp/clock-erp.env
ExecStart=/opt/clock-erp/venv/bin/python -m scripts.wb_orders_sync --mode fast
TimeoutStartSec=270
Nice=10
IOSchedulingClass=idle
UMask=0077
```

### vechasu-wb-sync.timer

```ini
[Unit]
Description=Vechasu WB fast timer

[Timer]
OnCalendar=*-*-* *:0/5:00
Persistent=true
Unit=vechasu-wb-sync.service

[Install]
WantedBy=timers.target
```

## Развёртывание и rollback

До deploy: обязательные PR checks, полный backup штатным механизмом, чистый production git status, итоговый diff. Проверить, что настроенный EnvironmentFile предоставляет WB_API_TOKEN, не читать и не раскрывать секрет вручную. CLI не загружает .env и не импортирует app.web.

После обновления кода скопировать четыре unit-файла в /etc/systemd/system, daemon-reload, enable --now vechasu-wb-sync.timer vechasu-wb-sync-full.timer. До запуска всех новых путей синхронизации Gunicorn должен загрузить новую блокировку; потребуется один штатный перезапуск приложения при обновлении кода. Сами timer приложение не перезапускают.

Снять read-only baseline и проверить после первого запуска: количество/уникальность WB-заказов, существующие mapping и sale_id, отсутствие новых WB-продаж и складских операций, статус wb:5681311322, диагностику и journal, HTTP 200, clock-erp active, timer active и next trigger.

При 500, SQLite lock, дублях или неконтролируемой нагрузке:

```sh
systemctl disable --now vechasu-wb-sync.timer vechasu-wb-sync-full.timer
systemctl stop vechasu-wb-sync.service vechasu-wb-sync-full.service
```

ERP продолжает работу с ручным sync. При необходимости отдельный revert PR к исходному 58750c0 с обычным безопасным deploy; БД не откатывать автоматически, чтобы не потерять текущие операции пользователей. Ни timer, ни rollback не должны вручную проводить или менять продажу wb:5681311322.
