from norless.config import NorlessConfig


def test_norless_config_loads_toml(tmp_path) -> None:
    config_path = tmp_path / 'norless.toml'
    config_path.write_text(
        """
state_dir = "~/state"
timeout = 15
debug = true
trash_maildir = "trash"

[[maildir]]
name = "inbox"
path = "~/Mail/inbox"

[[maildir]]
name = "trash"

[[account]]
name = "home"
host = "imap.example.com"
user = "alice"
password = "secret"
from = "alice@example.com"
trash = "Deleted"
smtp_host = "smtp.example.com"
smtp_port = 2525

[account.folders]
INBOX = "inbox"
""".strip()
    )

    config = NorlessConfig(str(config_path), one_thread=True, quiet=True)

    assert config.timeout == 15
    assert config.debug is True
    assert config.one_thread is True
    assert config.quiet is True
    assert config.state_dir.endswith('/state')
    assert config.maildirs['inbox'].path.endswith('/Mail/inbox')
    assert config.trash_maildir_config is not None
    assert config.trash_maildir_config.path == 'trash'

    account = config.accounts['home']
    assert account.host == 'imap.example.com'
    assert account.port == 993
    assert account.from_addr == 'alice@example.com'
    smtp_account = config.smtp_account_for('alice@example.com')
    assert smtp_account.port == 2525
    assert smtp_account.host == 'smtp.example.com'
    assert smtp_account.xoauth2 is None
    assert [(item.folder, item.maildir.name, item.trash) for item in config.sync_list] == [
        ('INBOX', 'inbox', 'Deleted')
    ]


def test_norless_config_filters_without_mutation_step(tmp_path) -> None:
    config_path = tmp_path / 'norless.toml'
    config_path.write_text(
        """
state_dir = "/tmp/state"

[[maildir]]
name = "inbox"

[[maildir]]
name = "alerts"

[[account]]
name = "home"
host = "imap.example.com"
user = "alice"
from = "alice@example.com"
smtp_host = "smtp.example.com"

[account.folders]
INBOX = "inbox"
Alerts = "alerts"

[[account]]
name = "work"
host = "imap.work.com"
user = "bob"
from = "bob@example.com"
smtp_host = "smtp.work.com"

[account.folders]
INBOX = "alerts"
""".strip()
    )

    config = NorlessConfig(str(config_path), account='home', maildir='alerts')

    assert set(config.accounts) == {'home'}
    assert set(config.maildirs) == {'alerts'}
    assert [(item.account, item.folder, item.maildir.name) for item in config.sync_list] == [
        ('home', 'Alerts', 'alerts')
    ]


def test_norless_config_uses_nested_xoauth2(tmp_path) -> None:
    config_path = tmp_path / 'norless.toml'
    config_path.write_text(
        """
state_dir = "/tmp/state"

[[maildir]]
name = "inbox"

[[account]]
name = "home"
host = "imap.example.com"
user = "alice"
from = "alice@example.com"
smtp_host = "smtp.example.com"

[account.xoauth2]
client_id = "cid"
secret = "sec"
refresh = "ref"

[account.folders]
INBOX = "inbox"
""".strip()
    )

    config = NorlessConfig(str(config_path))

    smtp_account = config.smtp_account_for('alice@example.com')
    assert smtp_account.xoauth2 is not None
    assert smtp_account.xoauth2.client_id == 'cid'
    assert smtp_account.xoauth2.secret == 'sec'
    assert smtp_account.xoauth2.refresh_token == 'ref'
