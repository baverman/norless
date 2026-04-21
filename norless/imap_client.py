import socket
import base64
import re
from dataclasses import dataclass
from sansproto import receiver, Reader, Parser, Emitter, Collector
from typing import Collection

BUFSIZE = 64 * 1024
TOKENS_RE = re.compile(rb'"(?:[^"\\]|\\.)*"|\(|\)|[^)\s]+')

RESP_TEXT = b'OK', b'BAD', b'NO', b'PREAUTH', b'BYE'
BAD_RESP_TEXT = b'BAD', b'NO', b'BYE'

Value = bytes | list['Value']


@dataclass
class Status:
    tag: bytes
    kind: bytes
    code: bytes
    payload: bytes
    text: bytes


@dataclass
class Response:
    data: list[list[Value]]
    status: Status


@dataclass
class Select:
    flags: list[bytes]
    exists: int
    recent: int
    uidvalidity: int
    uidnext: int
    permanentflags: list[bytes] | None = None
    unseen: int | None = None
    highestmodseq: int | None = None
    readonly: bool | None = None


def add_value(stack: list[list[Value]], value: Value) -> None:
    stack[-1].append(value)


def parse_resp_text(text: bytes) -> tuple[list[bytes], bytes]:
    if not text.startswith(b'['):
        return [], text

    code, _, rest = text[1:].partition(b']')
    name, sep, payload = code.partition(b' ')
    return ([name, payload] if sep else [name]), rest.lstrip()


def parse_status(line: list[Value]) -> Status:
    if line[0] == b'+':
        return Status(
            tag=b'+',
            kind=b'',
            code=b'',
            payload=b'',
            text=line[1] if len(line) > 1 else b'',  # type: ignore[arg-type]
        )

    code = line[2]
    return Status(
        tag=line[0],  # type: ignore[arg-type]
        kind=line[1],  # type: ignore[arg-type]
        code=code[0] if code else b'', # type: ignore[arg-type]
        payload=code[1] if len(code) > 1 else b'',  # type: ignore[arg-type]
        text=line[3],  # type: ignore[arg-type]
    )


def parse_flag_payload(payload: bytes) -> list[bytes]:
    return [tok for tok in TOKENS_RE.findall(payload) if tok not in (b'(', b')')]


def quote(value: bytes) -> bytes:
    return b'"' + value.replace(b'\\', b'\\\\').replace(b'"', b'\\"') + b'"'


@receiver
def proto(emit: Emitter[list[Value]]) -> Parser:
    reader = Reader()
    while True:
        lines = [(yield from reader.read_until(b'\r\n'))]
        # print('PROTO:', lines)
        result: list[Value] = []
        stack = [result]
        lnum = 0
        while lines:
            lnum += 1
            line = lines.pop(0)
            if not line:
                break

            if lnum == 1 and line.startswith(b'+'):
                result = line.split(None, 1)  # type: ignore[assignment]
                break

            tokens: list[bytes] = TOKENS_RE.findall(line)
            if lnum == 1 and len(tokens) >= 2 and tokens[1].upper() in RESP_TEXT:
                code, text = parse_resp_text(line.split(None, 2)[2] if len(tokens) > 2 else b'')
                result = [tokens[0], tokens[1], code, text]  # type: ignore[list-item]
                break

            if b'[' in line:
                ptokens = []
                itokens = iter(tokens)
                for tok in itokens:
                    if not tok.startswith(b'"') and b'[' in tok and b']' not in tok:
                        jtok = [tok]
                        for it in itokens:
                            jtok.append(it)
                            if b']' in it:
                                break
                        tok = b' '.join(jtok)
                    ptokens.append(tok)
            else:
                ptokens = tokens

            for idx, tok in enumerate(ptokens, 1):
                if tok.startswith(b'{') and idx == len(ptokens):
                    size = int(tok[1:-1])
                    tok = yield from reader.read(size)
                    lines.append((yield from reader.read_until(b'\r\n')))
                elif tok.startswith(b'"'):
                    tok = tok[1:-1].replace(b'\\"', b'"').replace(b'\\\\', b'\\')
                elif tok == b'(':
                    group: list[Value] = []
                    add_value(stack, group)
                    stack.append(group)
                    continue
                elif tok == b')':
                    stack.pop()
                    continue
                add_value(stack, tok)

        # print('EMIT:', result)
        emit(result)


class Proto:
    def __init__(self) -> None:
        self._counter = 0
        self._lines: list[list[Value]] = []
        self._lpos = 0
        self._receiver = proto(self._lines.append)
        self._current_tag = b''
        self.send = self._receiver.send

    def _tag(self) -> bytes:
        tag = self._current_tag = f'A{self._counter}'.encode()
        self._counter += 1
        return tag

    def wait_response(
        self, data: bytes, tag: bytes | None = None, status: bool = True
    ) -> Response | None:
        tag = tag or self._current_tag
        self.send(data)
        # print('##: ', self._lines)
        idx = self._lpos
        for idx in range(self._lpos, len(self._lines)):
            line = self._lines[idx]
            if len(line) >= 2 and line[1].upper() in BAD_RESP_TEXT:  # type: ignore[union-attr]
                line_status = parse_status(line)
                if line_status.tag == self._current_tag or line_status.kind.upper() == b'BYE':
                    raise ValueError(line_status.text)

            if line[0] == tag and (not status or (len(line) >= 2 and line[1].upper() in RESP_TEXT)):  # type: ignore[union-attr]
                result = self._lines[:idx]
                resp = self._lines[idx]
                self._lines[:] = self._lines[idx + 1 :]
                self._lpos = 0
                return Response(result, parse_status(resp))

        self._lpos = idx
        return None

    def command(self, cmd: str, data: Collection[bytes]) -> bytes:
        tag = self._current_tag = self._tag()
        payload = b' '.join((tag, cmd.encode(), *data)) + b'\r\n'
        return payload

    def collect_result(
        self, cmd: str, response: list[list[Value]], cmd_idx: int = 1
    ) -> list[list[Value]]:
        result = []
        ccmd = cmd.encode().upper()
        for it in response:
            if len(it) > cmd_idx and it[cmd_idx].upper() == ccmd:  # type: ignore[union-attr]
                result.append(it[cmd_idx + 1 :])
        return result

    def collect_list(self, response: Response) -> list[tuple[list[bytes], bytes, bytes]]:
        result: list[tuple[list[bytes], bytes, bytes]] = []
        for it in self.collect_result('LIST', response.data):
            result.append((it[0], it[1], it[2]))  # type: ignore[arg-type]
        return result

    def collect_search(self, response: Response) -> list[int]:
        result: list[int] = []
        for it in self.collect_result('SEARCH', response.data):
            result.extend(int(uid) for uid in it)  # type: ignore[arg-type]
        return result

    def collect_pairs(
        self, cmd: str, response: Response, cmd_idx: int = 1
    ) -> list[dict[str, Value]]:
        result: list[dict[str, Value]] = []
        for it in self.collect_result(cmd, response.data, cmd_idx=cmd_idx):
            result.append(item := {})
            k: bytes
            for k, v in zip(it[0][::2], it[0][1::2], strict=True):  #type: ignore[assignment]
                key = k.upper().decode()
                item[key] = v
                if cmd == 'FETCH' and key.startswith(('BODY[', 'BODY.')):
                    item['BODY'] = v
        return result

    def collect_select(self, response: Response) -> Select:
        flags: list[bytes] | None = None
        exists: int | None = None
        recent: int | None = None
        uidvalidity: int | None = None
        uidnext: int | None = None
        permanentflags: list[bytes] | None = None
        unseen: int | None = None
        highestmodseq: int | None = None
        readonly: bool | None = None

        for line in response.data:
            if len(line) >= 3 and line[1] == b'FLAGS':
                flags = line[2]  # type: ignore[assignment]
            elif len(line) >= 3 and line[2] == b'EXISTS':
                exists = int(line[1])  # type: ignore[arg-type]
            elif len(line) >= 3 and line[2] == b'RECENT':
                recent = int(line[1])  # type: ignore[arg-type]
            elif len(line) >= 4 and line[1] == b'OK':
                code = line[2]
                if code:
                    name = code[0]
                    payload = code[1] if len(code) > 1 else b''
                    if name == b'UIDVALIDITY':
                        uidvalidity = int(payload)  # type: ignore[arg-type]
                    elif name == b'UIDNEXT':
                        uidnext = int(payload)  # type: ignore[arg-type]
                    elif name == b'PERMANENTFLAGS':
                        permanentflags = parse_flag_payload(payload)  # type: ignore[arg-type]
                    elif name == b'UNSEEN':
                        unseen = int(payload)  # type: ignore[arg-type]
                    elif name == b'HIGHESTMODSEQ':
                        highestmodseq = int(payload)  # type: ignore[arg-type]

        if response.status.code == b'READ-WRITE':
            readonly = False
        elif response.status.code == b'READ-ONLY':
            readonly = True

        if (
            flags is None
            or exists is None
            or recent is None
            or uidvalidity is None
            or uidnext is None
        ):
            raise ValueError('incomplete SELECT response')

        return Select(
            flags=flags,
            exists=exists,
            recent=recent,
            uidvalidity=uidvalidity,
            uidnext=uidnext,
            permanentflags=permanentflags,
            unseen=unseen,
            highestmodseq=highestmodseq,
            readonly=readonly,
        )


class Client:
    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._proto = Proto()

        self._init()

    def _init(self) -> None:
        self._wait_response(b'*')

    def _wait_response(self, tag: bytes | None = None, status: bool = True) -> Response:
        while True:
            resp = self._proto.wait_response(self._sock.recv(BUFSIZE), tag, status)
            if resp:
                return resp

    def _send_command(self, cmd: str, data: Collection[bytes] = ()) -> None:
        self._sock.sendall(self._proto.command(cmd, data))

    def authenticate(self, mechanism: str, data: bytes) -> None:
        self._send_command('AUTHENTICATE', (mechanism.encode(),))
        resp = self._wait_response(b'+', status=False)
        # print('@@ cont', resp)
        self._sock.sendall(base64.b64encode(data) + b'\r\n')
        resp = self._wait_response()
        # print('@@ auth resp', resp)

    def login(self, username: bytes, password: bytes) -> None:
        self.command('LOGIN', (quote(username), quote(password)))

    def command(self, cmd: str, data: Collection[bytes] = (), uid: bool = False) -> Response:
        if uid:
            cmd = 'UID ' + cmd
        # print('@@', cmd, data)
        self._send_command(cmd, data)
        return self._wait_response()

    def select(self, mailbox: bytes) -> Select:
        return self._proto.collect_select(self.command('SELECT', (quote(mailbox),)))

    def search(self, criteria: bytes, uid: bool = False) -> list[int]:
        return self._proto.collect_search(self.command('SEARCH', (criteria,), uid=uid))

    def fetch(self, query: bytes, fields: bytes, uid: bool = False) -> list[dict[str, Value]]:
        resp = self.command('FETCH', (query, fields), uid=uid)
        return self._proto.collect_pairs('FETCH', resp, cmd_idx=2)

    def store(
        self, query: bytes, modifier: bytes, flags: bytes, uid: bool = False
    ) -> list[dict[str, Value]]:
        resp = self.command('STORE', (query, modifier, flags), uid=uid)
        return self._proto.collect_pairs('FETCH', resp, cmd_idx=2)

    def list_folders(
        self, directory: bytes = b'""', pattern: bytes = b'*'
    ) -> list[tuple[list[bytes], bytes, bytes]]:
        resp = self.command('LIST', (directory, pattern))
        return self._proto.collect_list(resp)


if __name__ == '__main__':
    import sys
    import ssl
    from norless import config

    cfg = config.NorlessConfig(sys.argv[1])
    acc = cfg.accounts['baverman']

    s = socket.create_connection((acc.host, acc.port))
    ctx = ssl.create_default_context()

    ss = ctx.wrap_socket(s, server_hostname=acc.host)
    c = Client(ss)

    print(c.command('CAPABILITY').data[-1])

    assert acc.xoauth2
    xo = 'user={}\x01auth=Bearer {}\x01\x01'.format(acc.username, acc.xoauth2.get_token()).encode()
    c.authenticate('XOAUTH2', xo)

    for it in c.list_folders():
        print(it)

    print(c.select(b'INBOX'))
    print()
    print(c.search(b'(UID 49000:*)', uid=True))
    print()
    for itf in c.fetch(b'49539', b'(FLAGS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID TO)] UID)', uid=True):
        print(itf)
        print()
