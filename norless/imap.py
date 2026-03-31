from __future__ import annotations
import re
import time
import imaplib
import ssl

from hashlib import sha1
from functools import cached_property
from typing import TYPE_CHECKING, TypedDict, Iterator, NamedTuple
from ssl import SSLSocket
from itertools import batched

from .utils import check_cert, nstr
from .maildir import Message

if TYPE_CHECKING:
    from .config import XOauth2Holder

LIST_REGEX = re.compile(rb'\((?P<flags>.*?)\) "(?P<sep>.*)" (?P<name>.*)')

ImapList = list[None] | list[bytes | tuple[bytes, bytes]]


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

    def get_status(self, folder: str) -> Status:
        result = self.client.status(self.client._quote(folder), '(MESSAGES UNSEEN UIDVALIDITY)')
        messages = int(get_field(result[1][0], 'MESSAGES'))
        unseen = int(get_field(result[1][0], 'UNSEEN'))
        uidvalidity = int(get_field(result[1][0], 'UIDVALIDITY'))
        return Status(messages, unseen, uidvalidity)

    def select(self, name: str) -> None:
        if name != self.selected_folder:
            self.client.select(self.client._quote(name))
            self.selected_folder = name


def message_id(msg: Message) -> str:
    msg_id = msg.get('message-id')
    if not msg_id:
        hsh = sha1(f'{msg["date"]}:{msg["from"]}:{msg["to"]}:{msg["subject"]}'.encode()).hexdigest()
        msg_id = f'<{hsh}@generated-missing>'
    return msg_id


def iter_result(imap_list: ImapList) -> Iterator[tuple[bytes, bytes]]:
    if imap_list and imap_list[0] is None:
        return

    it: Iterator[tuple[bytes, bytes]] = iter(imap_list)  # type: ignore[arg-type]
    for info, body in it:
        yield info, body
        next(it)


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
        suids = ','.join(map(str, uids))
        self.select()
        self.box.client.uid('COPY', suids, trash_folder)
        self.box.client.uid('STORE', suids, '+FLAGS', '(\\Deleted)')
        self.box.client.expunge()

    def delete(self, uids: list[int]) -> None:
        self.select()
        suids = ','.join(map(str, uids))
        self.box.client.uid('STORE', suids, '+FLAGS', '(\\Deleted)')

    def seen(self, uids: list[int]) -> None:
        suids = ','.join(map(str, uids))
        self.select()
        self.box.client.uid('STORE', suids, '+FLAGS', '(\\Seen)')

    def info(self, uids: list[int] | None = None, recent: int | None = None) -> Iterator[Info]:
        self.select()
        request = '(UID FLAGS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE FROM TO SUBJECT)])'

        if uids is not None:
            result = self.box.client.uid('fetch', ','.join(map(str, uids)), request)
        elif recent is not None:
            start, end = max(self.total - recent, 1), self.total
            result = self.box.client.fetch(f'{start}:{end}', request)
        else:
            result = self.box.client.fetch('1:*', request)

        # broken = []
        for info, body in iter_result(result[1]):
            uid = get_field(info, 'UID')
            flags = tuple(map(nstr, imaplib.ParseFlags(info)))
            msg = Message(body.replace(b'\r\n', b'\n'))
            # if message_id(msg)[0] != '<':
            #     broken.append(int(uid))
            yield Info(int(uid), message_id(msg), flags, msg)

        # if broken:
        #     print('  Cleanup:', len(broken))
        #     suids = ','.join(map(str, broken))
        #     self.box.client.uid('STORE', suids, '+FLAGS', '(\\Deleted)')
        #     self.box.client.expunge()

    def fetch(self, last_n: int, last_uid: int) -> Iterator[MsgDict]:
        self.select()

        if last_uid:
            new_uids_result = self.box.client.uid('search', '(UID {}:*)'.format(last_uid + 1))
            uids: list[int] = [int(r) for r in new_uids_result[1][0].split() if int(r) > last_uid]
            yield from self.fetch_uids(uids)
        else:
            if not self.total:
                return

            start, end = max(self.total - last_n, 1), self.total
            result = self.box.client.fetch('{}:{}'.format(start, end), '(UID FLAGS BODY.PEEK[])')

            yield from self._iter_fetch(result[1])

    def fetch_uids(self, uids: list[int]) -> Iterator[MsgDict]:
        if not uids:
            return

        self.select()

        for batch in batched(uids, 100):
            result = self.box.client.uid(
                'fetch', ','.join(map(str, batch)), '(UID FLAGS BODY.PEEK[])'
            )
            yield from self._iter_fetch(result[1])

    def _iter_fetch(self, imap_list: ImapList) -> Iterator[MsgDict]:
        for info, msg in iter_result(imap_list):
            r: MsgDict = {
                'uid': get_field(info, 'UID'),
                'flags': tuple(map(nstr, imaplib.ParseFlags(info))),
                'body': msg,
            }
            yield r

    def get_flags(self, uids: list[int]) -> dict[int, tuple[str, ...]]:
        result = self.box.client.uid('fetch', ','.join(map(str, uids)), '(UID FLAGS)')
        flags = {}
        for info in result[1]:
            if not info:
                continue
            flags[int(get_field(info, 'UID'))] = tuple(map(nstr, imaplib.ParseFlags(info)))

        return flags

    def append_messages(self, messages: list[Message], last_uid: int) -> list[tuple[int, str]]:
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

    def unseen_uids(self) -> list[int]:
        self.select()
        resp = self.box.client.uid('search', 'UNSEEN')
        return [int(r) for r in resp[1][0].split()]
