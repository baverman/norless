from dataclasses import dataclass
from .schema import field, as_kv, as_list, optfield


@dataclass
class MaildirConfig:
    name: str = field(str)
    _path: str | None = optfield(str, src='path')
    mark_as_seen: bool = field(bool, False)

    @property
    def path(self) -> str:
        return self._path or self.name


@dataclass
class XOAuth2Config:
    client_id: str = field(str)
    secret: str = field(str)
    refresh: str = field(str)


@dataclass
class AccountConfig:
    name: str = field(str)
    from_addr: str = field(str, src='from')
    trash: str | None = optfield(str)

    host: str = field(str)
    port: int | None = optfield(int)
    cafile: str | None = optfield(str)

    smtp_host: str = field(str)
    smtp_port: int | None = optfield(int)
    smtp_cafile: str | None = optfield(str)

    user: str = field(str)
    password: str | None = optfield(str)

    xoauth2: XOAuth2Config | None = optfield(XOAuth2Config)

    folders: dict[str, str] = field(as_kv(str))


@dataclass
class Config:
    state_dir: str = field(str)
    timeout: int = field(int, 60)
    trash_maildir: str | None = optfield(str)
    debug: bool = field(bool, False)

    maildirs: list[MaildirConfig] = field(as_list(MaildirConfig), src='maildir')
    accounts: list[AccountConfig] = field(as_list(AccountConfig), src='account')
