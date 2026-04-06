import sys
import fcntl
import ssl

from contextlib import contextmanager, AbstractContextManager
from email.header import decode_header
from subprocess import PIPE, Popen
from typing import Iterator, Protocol

def dheader(header: str) -> str:
    result: list[str] = []
    for data, enc in decode_header(header):
        if enc:
            data = data.decode(enc)
        else:
            try:
                data = data.decode('ascii')
            except UnicodeDecodeError:
                data = data.decode('latin1', 'replace')

        result.append(data)

    return ''.join(result)


class FileLockT(Protocol):
    def __call__(self, block: bool = False) -> AbstractContextManager[None]: ...


def FileLock(fname: str) -> FileLockT:
    @contextmanager
    def inner(block: bool = False) -> Iterator[None]:
        fp = open(fname, 'w')

        opts = fcntl.LOCK_EX
        if not block:
            opts |= fcntl.LOCK_NB

        try:
            fcntl.lockf(fp, opts)
        except IOError:
            print('Another instance already running', file=sys.stderr)
            sys.exit(2)

        yield

    return inner


def check_cert(data: bytes, cafile: str | None = None) -> None:
    cmd = ['openssl', 'verify']
    if cafile:
        cmd.extend(('-CAfile', cafile))

    p = Popen(cmd, stdout=PIPE, stderr=PIPE, stdin=PIPE)
    out, err = p.communicate(data)
    if p.returncode:
        raise ssl.SSLError(out + err)
