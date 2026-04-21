from __future__ import annotations

import socket
import ssl

from functools import cached_property
from hashlib import sha1
from itertools import batched
from ssl import SSLSocket
from typing import TYPE_CHECKING, Iterator, NamedTuple, TypedDict

from .imap_client import Client
from .imap_client import Select as ImapSelect
from .maildir import Message
from .utils import check_cert

if TYPE_CHECKING:
    from .config import XOauth2Holder


class Info(NamedTuple):
    uid: int
    msgid: str
    flags: tuple[str, ...]
    msg: Message


class Status(NamedTuple):
    messages: int
    unseen: int
    uidvalidity: int


class MsgDict(TypedDict):
    uid: str
    flags: tuple[str, ...]
    body: bytes


def get_cert(sock: SSLSocket) -> bytes:
    result = sock.getpeercert(True)
    assert result
    return result


def message_id(msg: Message) -> str:
    msg_id = msg.get('message-id')
    if not msg_id:
        hsh = sha1(f'{msg["date"]}:{msg["from"]}:{msg["to"]}:{msg["subject"]}'.encode()).hexdigest()
        msg_id = f'<{hsh}@generated-missing>'
    return msg_id


class ImapBox:
    selected_folder: str | None
    name: str
    from_addr: str | None

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int | None = None,
        ssl: bool = True,
        fingerprint: str | None = None,
        cafile: str | None = None,
        debug: int | None = None,
        xoauth2: XOauth2Holder | None = None,
    ) -> None:
        self.host = host
        self.port = port or (993 if ssl else 143)
        self.username = username
        self.password = password
        self.ssl = ssl
        self.fingerprint = fingerprint
        self.cafile = cafile
        self.debug = debug
        self.xoauth2 = xoauth2

        self.selected_folder = None
        self._selected: ImapSelect | None = None

    def get_fingerprint(self, cert: bytes) -> str:
        s = sha1(cert).hexdigest().upper()
        return ':'.join(s[i : i + 2] for i in range(0, len(s), 2))

    @cached_property
    def client(self) -> Client:
        sock = socket.create_connection((self.host, self.port))
        if self.ssl:
            ctx = ssl.create_default_context()
            wrapped = ctx.wrap_socket(sock, server_hostname=self.host)
            if self.fingerprint:
                server_fingerprint = self.get_fingerprint(get_cert(wrapped))
                if server_fingerprint != self.fingerprint:
                    raise Exception(
                        f'Mismatched fingerprint for {self.host} {server_fingerprint}'
                    )
            elif self.cafile:
                cert = ssl.DER_cert_to_PEM_cert(get_cert(wrapped))
                check_cert(cert.encode(), self.cafile)
            sock = wrapped

        client = Client(sock)
        if self.xoauth2:
            xo = 'user={}\x01auth=Bearer {}\x01\x01'.format(
                self.username, self.xoauth2.get_token()
            ).encode()
            client.authenticate('XOAUTH2', xo)
        else:
            client.login(self.username.encode(), self.password.encode())
        return client

    def list_folders(self) -> list[tuple[str, str, str]]:
        result: list[tuple[str, str, str]] = []
        for flags, sep, name in self.client.list_folders():
            result.append(
                (
                    ' '.join(flag.decode('latin-1') for flag in flags),
                    sep.decode('latin-1'),
                    name.decode('latin-1'),
                )
            )
        return result

    def get_folder(self, name: str) -> Folder:
        return Folder(self, name)

    def get_status(self, folder: str) -> Status:
        self.select(folder)
        assert self._selected is not None
        return Status(self._selected.exists, self._selected.unseen or 0, self._selected.uidvalidity)

    def select(self, name: str) -> None:
        if name != self.selected_folder:
            self._selected = self.client.select(name.encode())
            self.selected_folder = name


class Folder:
    def __init__(self, box: ImapBox, name: str) -> None:
        self.box = box
        self.name = name

    @cached_property
    def status(self) -> Status:
        return self.box.get_status(self.name)

    @property
    def total(self) -> int:
        return self.status.messages

    @property
    def new(self) -> int:
        return self.status.unseen

    @property
    def uidvalidity(self) -> int:
        return self.status.uidvalidity

    def select(self) -> None:
        self.box.select(self.name)

    def trash(self, uids: list[int], trash_folder: str) -> None:
        raise NotImplementedError('trash is not implemented in imap2 yet')

    def delete(self, uids: list[int]) -> None:
        self.select()
        self.box.client.store(','.join(map(str, uids)).encode(), b'+FLAGS', b'(\\Deleted)', uid=True)

    def seen(self, uids: list[int]) -> None:
        self.select()
        self.box.client.store(','.join(map(str, uids)).encode(), b'+FLAGS', b'(\\Seen)', uid=True)

    def info(self, uids: list[int] | None = None, recent: int | None = None) -> Iterator[Info]:
        self.select()
        request = b'(UID FLAGS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE FROM TO SUBJECT)])'

        if uids is not None:
            result = self.box.client.fetch(','.join(map(str, uids)).encode(), request, uid=True)
        elif recent is not None:
            start, end = max(self.total - recent, 1), self.total
            result = self.box.client.fetch(f'{start}:{end}'.encode(), request)
        else:
            result = self.box.client.fetch(b'1:*', request)

        for item in result:
            uid = int(item['UID'])  # type: ignore[arg-type]
            flags = tuple(flag.decode('latin-1') for flag in item['FLAGS'])  # type: ignore[union-attr]
            msg = Message(item['BODY'].replace(b'\r\n', b'\n'))  # type: ignore[union-attr]
            yield Info(uid, message_id(msg), flags, msg)

    def fetch_uids(self, uids: list[int]) -> Iterator[MsgDict]:
        if not uids:
            return

        self.select()
        for batch in batched(uids, 100):
            result = self.box.client.fetch(
                ','.join(map(str, batch)).encode(), b'(UID FLAGS BODY.PEEK[])', uid=True
            )
            for item in result:
                yield {
                    'uid': item['UID'].decode('latin-1'),  # type: ignore[union-attr]
                    'flags': tuple(flag.decode('latin-1') for flag in item['FLAGS']),  # type: ignore[union-attr]
                    'body': item['BODY'],  # type: ignore[typeddict-item]
                }

    def get_flags(self, uids: list[int]) -> dict[int, tuple[str, ...]]:
        raise NotImplementedError('get_flags is not implemented in imap2 yet')

    def append_messages(self, messages: list[Message], last_uid: int) -> list[tuple[int, str]]:
        raise NotImplementedError('append_messages is not implemented in imap2 yet')

    def uids_since(self, last_uid: int) -> list[int]:
        self.select()
        return [uid for uid in self.box.client.search(f'(UID {last_uid + 1}:*)'.encode(), uid=True) if uid > last_uid]
