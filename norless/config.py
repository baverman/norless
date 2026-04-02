from __future__ import annotations

import os.path
import re
import time
import tomllib
from dataclasses import dataclass

from .config_model import AccountConfig, Config, MaildirConfig
from .imap import ImapBox
from .schema import parse
from .utils import FileLock, FileLockT

SYNC_RE = re.compile('->')


@dataclass(frozen=True)
class Sync:
    account: str
    folder: str
    maildir: MaildirConfig
    trash: str = 'Trash'


@dataclass(frozen=True)
class SmtpAccount:
    from_addr: str
    host: str
    port: int
    user: str
    password: str | None
    xoauth2: XOauth2Holder | None


class XOauth2Holder:
    def __init__(
        self, state_dir: str, account: str, client_id: str, secret: str, refresh_token: str
    ):
        self.fname = os.path.join(state_dir, account + '.token')
        self.client_id = client_id
        self.secret = secret
        self.refresh_token = refresh_token

    def _cached_token(self) -> str | None:
        if os.path.exists(self.fname) and os.path.getctime(self.fname) > time.time():
            return open(self.fname).read()
        return None

    def get_token(self) -> str:
        token = self._cached_token()
        if not token:
            from . import gmail

            info = gmail.refresh_token(self.client_id, self.secret, self.refresh_token)
            token = info['access_token']
            expire = time.time() + info['expires_in'] * 0.9
            with open(self.fname, 'w') as f:
                f.write(token)
            os.utime(self.fname, (expire, expire))

        return token


def load_toml(fname: str) -> Config:
    with open(fname, 'rb') as f:
        return parse(Config, tomllib.load(f))


class NorlessConfig:
    raw: Config
    quiet: bool
    app_lock: FileLockT
    one_thread: bool

    def __init__(
        self,
        fname: str,
        *,
        account: str | None = None,
        maildir: str | None = None,
        one_thread: bool = False,
        quiet: bool = False,
    ) -> None:
        self.raw = load_toml(fname)
        self.state_dir = os.path.expanduser(self.raw.state_dir)
        self.timeout = self.raw.timeout
        self.debug = self.raw.debug
        self.one_thread = one_thread
        self.quiet = quiet
        self.app_lock = FileLock(os.path.join(self.state_dir, '.norless-lock'))

        all_maildirs = {m.name: m for m in self.raw.maildirs}
        self.maildirs = {
            name: cfg for name, cfg in all_maildirs.items() if maildir is None or name == maildir
        }
        if self.raw.trash_maildir is None:
            self.trash_maildir_config = None
        else:
            self.trash_maildir_config = MaildirConfig(
                self.raw.trash_maildir,
                None,
                False,
            )

        self.accounts: dict[str, ImapBox] = {}
        self.smtp_accounts: dict[str, SmtpAccount] = {}
        self.sync_list: list[Sync] = []

        for cfg in self.raw.accounts:
            if account is not None and cfg.name != account:
                continue

            xoauth2 = self._build_xoauth2(cfg)

            box = ImapBox(
                cfg.host,
                cfg.user,
                cfg.password or '',
                cfg.port,
                True,
                None,
                os.path.expanduser(cfg.cafile) if cfg.cafile else None,
                int(self.debug),
                xoauth2,
            )
            box.name = cfg.name
            box.from_addr = cfg.from_addr
            self.accounts[cfg.name] = box

            self.smtp_accounts[cfg.from_addr] = SmtpAccount(
                cfg.from_addr,
                cfg.smtp_host,
                cfg.smtp_port or 587,
                cfg.user,
                cfg.password,
                xoauth2,
            )

            for folder, maildir_name in cfg.folders.items():
                if maildir is not None and maildir_name != maildir:
                    continue
                try:
                    sync_maildir = self.maildirs[maildir_name]
                except KeyError as e:
                    raise ValueError(
                        f'Unknown maildir {maildir_name!r} for account {cfg.name!r}'
                    ) from e
                self.sync_list.append(Sync(cfg.name, folder, sync_maildir, cfg.trash or 'Trash'))

    def _build_xoauth2(self, cfg: AccountConfig) -> XOauth2Holder | None:
        if cfg.xoauth2 is None:
            return None
        return XOauth2Holder(
            self.state_dir,
            cfg.name,
            cfg.xoauth2.client_id,
            cfg.xoauth2.secret,
            cfg.xoauth2.refresh,
        )

    def sync_by_account(self) -> dict[str, list[Sync]]:
        result: dict[str, list[Sync]] = {}
        for s in self.sync_list:
            result.setdefault(s.account, []).append(s)
        return result
