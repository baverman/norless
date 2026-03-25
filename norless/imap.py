from __future__ import annotations
import re
import time
import imaplib
import ssl

from hashlib import sha1
from mailbox import Message
from functools import cached_property
from typing import TYPE_CHECKING, TypedDict
from ssl import SSLSocket

from .utils import check_cert, nstr

if TYPE_CHECKING:
    from .config import XOauth2Holder
    from .maildir import MaildirMessage

LIST_REGEX = re.compile(rb'\((?P<flags>.*?)\) "(?P<sep>.*)" (?P<name>.*)')


class MsgDict(TypedDict):
    uid: str
    flags: tuple[str, ...]
    body: bytes


def get_field(info: bytes, field: str) -> str:
    bfield = field.encode()
    idx = info.index(bfield + b' ')
    return nstr(info[idx + len(bfield) :].split()[0].strip(b')'))


def get_cert(sock: SSLSocket) -> bytes:
    result = sock.getpeercert(True)
    assert result
    return result


def xoauth2_login(client: imaplib.IMAP4, username: str, xoauth2: XOauth2Holder) -> None:
    def xoauth(data: bytes) -> bytes:
        return 'user={}\x01auth=Bearer {}\x01\x01'.format(username, xoauth2.get_token()).encode()

    client.authenticate('XOAUTH2', xoauth)


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

        self.selected_folder = None
        self.xoauth2 = xoauth2

    def get_fingerprint(self, cert: bytes) -> str:
        s = sha1(cert).hexdigest().upper()
        return ':'.join(s[i : i + 2] for i in range(0, len(s), 2))

    @cached_property
    def client(self) -> imaplib.IMAP4:
        C = imaplib.IMAP4_SSL if self.ssl else imaplib.IMAP4
        cl = C(self.host, self.port)

        if self.ssl:
            if self.fingerprint:
                server_fingerprint = self.get_fingerprint(get_cert(cl.sock))  # type: ignore[arg-type]
                if server_fingerprint != self.fingerprint:
                    raise Exception(
                        'Mismatched fingerprint for {} {}'.format(self.host, server_fingerprint)
                    )
            elif self.cafile:
                cert = ssl.DER_cert_to_PEM_cert(get_cert(cl.sock))  # type: ignore[arg-type]
                check_cert(cert.encode(), self.cafile)

        if self.debug:
            cl.debug = self.debug

        if self.xoauth2:
            xoauth2_login(cl, self.username, self.xoauth2)
        else:
            cl.login(self.username, self.password)
        return cl

    def list_folders(self) -> list[tuple[str, str, str]]:
        result: list[tuple[str, str, str]] = []
        resp: tuple[str, list[bytes]] = self.client.list()  # type: ignore[assignment]
        for item in resp[1]:
            m = LIST_REGEX.search(item)
            if m:
                flags, sep, name = m.group('flags', 'sep', 'name')
                name = nstr(name).strip('"')
                result.append((nstr(flags), nstr(sep), name))

        return result

    def get_folder(self, name: str) -> Folder:
        return Folder(self, name)

    def get_status(self, folder: str) -> tuple[int, int]:
        result = self.client.status(self.client._quote(folder), '(MESSAGES UNSEEN)')
        messages = int(get_field(result[1][0], 'MESSAGES'))
        unseen = int(get_field(result[1][0], 'UNSEEN'))
        return messages, unseen

    def select(self, name: str) -> None:
        if name != self.selected_folder:
            self.client.select(self.client._quote(name))
            self.selected_folder = name


class Folder:
    def __init__(self, box: ImapBox, name: str) -> None:
        self.box = box
        self.name = name

        self._total: int | None = None
        self._new: int | None = None

    @property
    def total(self) -> int:
        if self._total is None:
            self.refresh()
            assert self._total
        return self._total

    @property
    def new(self) -> int:
        if self._new is None:
            self.refresh()
            assert self._new
        return self._new

    def refresh(self) -> None:
        self._total, self._new = self.box.get_status(self.name)

    def select(self) -> None:
        self.box.select(self.name)

    def trash(self, uids: list[int], trash_folder: str) -> None:
        # import logging
        # logging.error('TRASH %s %s', self.name, uids)
        suids = ','.join(map(str, uids))
        self.select()
        self.box.client.uid('COPY', suids, trash_folder)
        self.box.client.uid('STORE', suids, '+FLAGS', '(\\Deleted)')
        self.box.client.expunge()

    def seen(self, uids: list[int]) -> None:
        suids = ','.join(map(str, uids))
        self.select()
        self.box.client.uid('STORE', suids, '+FLAGS', '(\\Seen)')

    def fetch(self, last_n: int | None = None, last_uid: int | None = None) -> list[MsgDict]:
        assert last_n or last_uid
        self.select()
        result_messages = []

        if last_uid:
            result = self.box.client.uid('search', '(UID {}:*)'.format(last_uid + 1))
            uids: list[bytes] = [r for r in result[1][0].split() if int(r) > last_uid]
            if not uids:
                return []

            result = self.box.client.uid(
                'fetch', b','.join(uids).decode(), '(UID FLAGS BODY.PEEK[])'
            )
        elif last_n:
            if not self.total:
                return []

            start, end = max(self.total - last_n, 1), self.total
            result = self.box.client.fetch('{}:{}'.format(start, end), '(UID FLAGS BODY.PEEK[])')

        it = iter(result[1])
        for info, msg in it:
            r: MsgDict = {
                'uid': get_field(info, 'UID'),
                'flags': tuple(map(nstr, imaplib.ParseFlags(info))),
                'body': msg.replace(b'\r\n', b'\n'),
            }
            result_messages.append(r)
            next(it)

        return result_messages

    def get_flags(self, uids: list[int]) -> dict[int, tuple[str, ...]]:
        result = self.box.client.uid('fetch', ','.join(map(str, uids)), '(UID FLAGS)')
        flags = {}
        for info in result[1]:
            if not info:
                continue
            flags[int(get_field(info, 'UID'))] = tuple(map(nstr, imaplib.ParseFlags(info)))

        return flags

    def append_messages(
        self, messages: list[MaildirMessage], last_uid: int
    ) -> list[tuple[int, str]]:
        self.select()
        for msg in messages:
            del msg['X-Norless-Id']
            msg['X-Norless-Id'] = msg.msgkey

            del msg['Message-ID']
            msg['Message-ID'] = msg.msgkey

            self.box.client.append(
                self.box.client._quote(self.name),
                '(\\Seen)',
                time.time(),  # type: ignore[arg-type]
                msg.as_bytes(),
            )

        result = self.box.client.uid('search', '(UID {}:*)'.format(last_uid + 1))

        uids: list[bytes] = [r for r in result[1][0].split() if int(r) > last_uid]
        stored_messages = []
        if uids:
            result = self.box.client.uid(
                'fetch', b','.join(uids).decode(), '(UID BODY.PEEK[HEADER])'
            )

            it = iter(result[1])
            for info, body in it:
                uid = get_field(info, 'UID')
                smsg = Message(body.replace(b'\r\n', b'\n'))

                if 'X-Norless-Id' in smsg:
                    stored_messages.append((int(uid), smsg['X-Norless-Id'].strip()))

                if 'Message-ID' in smsg:
                    stored_messages.append((int(uid), smsg['Message-ID'].strip()))

                next(it)

        return stored_messages
