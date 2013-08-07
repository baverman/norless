import sys
import fcntl

from time import time as ttime
from contextlib import contextmanager
from email.header import decode_header

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
    print msg, ttime() - t
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
            print >>sys.stderr, 'Another instance already running'
            sys.exit(2)

        yield

    return inner

