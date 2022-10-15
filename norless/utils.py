import sys
import fcntl
import ssl

from time import time as ttime
from contextlib import contextmanager
from email.header import decode_header
from subprocess import PIPE, Popen

btype = type(b'')
ntype = type('')
utype = type(u'')


def bstr(s, encoding='latin-1'):
    if type(s) is utype:
        return s.encode(encoding)
    return s


def nstr(s, encoding='latin-1'):
    t = type(s)
    if t is not ntype:
        if t is btype:
            return s.decode(encoding)
        elif t is utype:
            return s.encode(encoding)
    return s


def cached_property(func):
    name = '_' + func.__name__
    def inner(self):
        try:
            return getattr(self, name)
        except AttributeError:
            pass

        result = func(self)
        setattr(self, name, result)
        return result

    return property(inner)


def dheader(header):
    result = u''
    for data, enc in decode_header(header):
        if enc:
            data = data.decode(enc)
        else:
            try:
                data = data.decode('ascii')
            except UnicodeDecodeError:
                data = data.decode('latin1', 'replace')

        result += data

    return result


@contextmanager
def profileit(msg='profile'):
    t = ttime()
    yield
    print(msg, ttime() - t)
__builtins__['profileit'] = profileit


def FileLock(fname):
    @contextmanager
    def inner(block=False):
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


def check_cert(data, cafile=None):
    cmd = ['openssl', 'verify']
    if cafile:
        cmd.extend(('-CAfile', cafile))

    p = Popen(cmd, stdout=PIPE, stderr=PIPE, stdin=PIPE)
    out, err = p.communicate(data)
    if p.returncode:
        raise ssl.SSLError(out + err)
