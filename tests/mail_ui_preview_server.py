"""Isolated fixture server for the ERP mail responsive browser checks."""

from __future__ import print_function

import base64
import os
import sys
import tempfile
from email.message import EmailMessage
from pathlib import Path


PREVIEW_TEMP = tempfile.TemporaryDirectory(prefix="clock-erp-mail-preview-")
PREVIEW_ROOT = Path(PREVIEW_TEMP.name)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.update({
    "CATALOG_DATABASE_PATH": str(PREVIEW_ROOT / "catalog.db"),
    "ERP_AUTH_DATABASE": str(PREVIEW_ROOT / "auth.db"),
    "ORDERS_DATABASE_PATH": str(PREVIEW_ROOT / "orders.db"),
    "ERP_TASKS_DATABASE": str(PREVIEW_ROOT / "tasks.db"),
    "ERP_PURCHASES_DATABASE": str(PREVIEW_ROOT / "purchases.db"),
    "CUSTOMERS_DATABASE_PATH": str(PREVIEW_ROOT / "customers.db"),
    "ERP_SERVICES_DATABASE": str(PREVIEW_ROOT / "services.db"),
    "ERP_MAIL_DATABASE": str(PREVIEW_ROOT / "mail.db"),
    "ERP_MAIL_ATTACHMENT_ROOT": str(PREVIEW_ROOT / "mail-attachments"),
    "ERP_MAIL_SECRET_KEY": base64.urlsafe_b64encode(b"m" * 32).decode("ascii"),
    "ERP_SECRET_KEY": "mail-preview-session-secret",
    "SERVICE_VAULT_KEY": base64.urlsafe_b64encode(b"v" * 32).decode("ascii"),
    "ERP_SESSION_COOKIE_SECURE": "0",
    "ERP_AUTH_ENABLED": "0",
    "ERP_TEST_MODE": "1",
})

from app.catalog_db import CatalogDatabase  # noqa: E402
from app.customer_registry_migrations import migrate_database as migrate_customers  # noqa: E402
from app.domain_schema_migrations import apply_domain_migrations  # noqa: E402
from app.mail_migrations import migrate_database as migrate_mail  # noqa: E402
from app.purchases_migrations import migrate_database as migrate_purchases  # noqa: E402
from app.schema_migrations import apply_migrations  # noqa: E402
from scripts.migrate_services_vault import apply as migrate_services  # noqa: E402


apply_migrations(PREVIEW_ROOT / "catalog.db", app_commit="mail-ui-preview")
apply_domain_migrations(PREVIEW_ROOT / "auth.db", "auth", "mail-ui-preview")
apply_domain_migrations(PREVIEW_ROOT / "orders.db", "orders", "mail-ui-preview")
apply_domain_migrations(PREVIEW_ROOT / "tasks.db", "tasks", "mail-ui-preview")
migrate_purchases(PREVIEW_ROOT / "purchases.db")
migrate_customers(PREVIEW_ROOT / "customers.db")
migrate_services(PREVIEW_ROOT / "services.db")
migrate_mail(PREVIEW_ROOT / "mail.db")

from app import web  # noqa: E402
from app.services.mail import MailStore, SecretBox, parse_message  # noqa: E402


ROLE = os.environ.get("MAIL_PREVIEW_ROLE", "admin")
web.app.config.update(
    TESTING=True,
    AUTH_TESTING=False,
    SESSION_COOKIE_SECURE=False,
    MAIL_DATABASE=str(PREVIEW_ROOT / "mail.db"),
    MAIL_ATTACHMENT_ROOT=str(PREVIEW_ROOT / "mail-attachments"),
)
web.auth_is_enabled = lambda: True
web.current_auth_user = lambda: {
    "id": 1,
    "role": ROLE,
    "first_name": "Максим" if ROLE == "admin" else "Анна",
    "last_name": "Preview",
    "email": "preview@example.test",
}
web._task_users = lambda: []


def fixture_message():
    message = EmailMessage()
    message["Message-ID"] = "<mail-ui-preview@example.test>"
    message["From"] = (
        "Клиент с очень длинным именем "
        "<customer-with-a-long-address@example.test>"
    )
    message["To"] = "erp@example.test"
    message["Subject"] = (
        "Очень длинная тема письма для проверки безопасного сокращения "
        "в рабочем списке без горизонтального переполнения"
    )
    message["Date"] = "Fri, 28 Aug 2026 12:00:00 +0300"
    message.set_content("Тестовая переписка для изолированного preview.")
    return message.as_bytes()


if os.environ.get("MAIL_PREVIEW_CONNECTED") == "1":
    store = MailStore(PREVIEW_ROOT / "mail.db", PREVIEW_ROOT / "mail-attachments")
    account = store.save_account({
        "mailbox_name": "Общий рабочий ящик",
        "sender_name": "Vechasu ERP",
        "email": "erp@example.test",
        "imap_host": "imap.example.test",
        "imap_port": 993,
        "imap_security": "ssl",
        "smtp_host": "smtp.example.test",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "login": "erp@example.test",
        "password": "preview-app-password",
    }, 1, SecretBox())
    store.ingest(
        account["id"], "inbox", "INBOX", "1", 1,
        parse_message(fixture_message()),
    )
    with store.connect() as connection:
        connection.execute(
            "UPDATE mail_accounts SET initial_sync_complete=1,"
            "last_sync_status='ok',last_sync_at='2026-08-28T12:00:00+00:00'"
        )
        connection.commit()


@web.app.after_request
def inject_mail_ui_probe(response):
    if (
        web.request.path != "/app/mail"
        or not web.request.args.get("mail_ui_e2e")
        or not response.content_type.startswith("text/html")
    ):
        return response
    source = response.get_data(as_text=True)
    probe = r"""
<script>
(function(){
  function visible(node){return !!node&&getComputedStyle(node).display!=="none"&&node.getBoundingClientRect().width>0;}
  function inside(rect){return rect.left>=-1&&rect.top>=-1&&rect.right<=innerWidth+1&&rect.bottom<=innerHeight+1;}
  function overlap(a,b){return !!a&&!!b&&a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;}
  var mode=new URLSearchParams(location.search).get("mail_ui_e2e");
  var body=document.body;
  if(mode==="wizard"){
    document.getElementById("openConnectionWizard").click();
    document.querySelector('[data-provider="yandex"]').click();
    document.getElementById("wizardNext").click();
    var form=document.getElementById("mailSettingsForm");
    form.mailbox_name.value="Общий";form.sender_name.value="ERP";
    form.email.value="erp@example.test";form.login.value="erp@example.test";
    form.password.value="not-a-real-password";
    document.getElementById("wizardNext").click();
  }
  if(mode==="empty"&&window.VechasuNotify){window.VechasuNotify.success("Проверка уведомления");}
  setTimeout(function(){
    var wizard=document.getElementById("mailConnectionWizard");
    var connect=document.getElementById("openConnectionWizard");
    var toast=document.querySelector(".erp-toast");
    var ids=Array.from(document.querySelectorAll("[id]")).map(function(n){return n.id;});
    var unique=new Set(ids).size===ids.length;
    var labelled=Array.from(document.querySelectorAll("#mailSettingsForm input:not([type=hidden]),#mailSettingsForm select")).every(function(n){return !!n.closest("label");});
    body.dataset.mailOverflow=document.documentElement.scrollWidth<=innerWidth+1?"pass":"fail";
    body.dataset.mailEmpty=(mode!=="empty"||(visible(document.getElementById("connectionState"))&&!visible(document.getElementById("mailWorkspace"))&&!visible(document.getElementById("mailHeaderActions"))))?"pass":"fail";
    body.dataset.mailConnectButton=(mode!=="empty"||(visible(connect)&&inside(connect.getBoundingClientRect())))?"pass":"fail";
    body.dataset.mailToast=(mode!=="empty"||(toast&&inside(toast.getBoundingClientRect())&&!overlap(toast.getBoundingClientRect(),connect.getBoundingClientRect())))?"pass":"fail";
    body.dataset.mailWizard=(mode!=="wizard"||(wizard.open&&inside(wizard.getBoundingClientRect())&&visible(document.querySelector('[data-wizard-step="3"]'))))?"pass":"fail";
    body.dataset.mailFocus=(mode!=="wizard"||wizard.contains(document.activeElement))?"pass":"fail";
    body.dataset.mailA11y=(unique&&labelled&&(!wizard||wizard.getAttribute("aria-labelledby")))?"pass":"fail";
    body.dataset.mailEmployee=(mode!=="employee"||(!document.getElementById("openConnectionWizard")&&document.body.textContent.indexOf("Обратитесь к владельцу системы")>=0))?"pass":"fail";
    body.dataset.mailConnected=(mode!=="connected"||(visible(document.getElementById("mailWorkspace"))&&!visible(document.getElementById("connectionState"))&&document.getElementById("mailPagination").hidden))?"pass":"fail";
  },1200);
})();
</script>
"""
    response.set_data(source.replace("</body>", probe + "</body>"))
    return response


if __name__ == "__main__":
    web.app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PREVIEW_PORT", "5050")),
        debug=False,
        use_reloader=False,
    )
