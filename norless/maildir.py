import os
import errno
import socket

from time import time
from threading import RLock
from tempfile import mkstemp
from os.path import join, exists, isfile, basename

from email.message import Message
from mailbox import MaildirMessage


def parse_info(info):
    if info:
        _, _, flags = info.partition(',')
        return flags

    return ''


class Maildir(object):
    def __init__(self, path, create=True, msg_mode=0o600, dir_mode=0o700):
        self.path = path
        self.msg_mode = msg_mode
        self.dir_mode = dir_mode

        self.lock = RLock()

        self.path_new = join(path, 'new')
        self.path_cur = join(path, 'cur')
        self.path_tmp = join(path, 'tmp')

        self._counter = 0
        self._host = socket.gethostname().replace('.', '-').replace(':', '-')
        self._pid = os.getpid()

        if create:
            with self.lock:
                if not exists(self.path):
                    os.makedirs(self.path, self.dir_mode)

                for p in (self.path_new, self.path_cur, self.path_tmp):
                    if not exists(p):
                        os.mkdir(p, self.dir_mode)

    @property
    def toc(self):
        try:
            return self._toc
        except AttributeError:
            pass

        with self.lock:
            toc = {}
            for path in (self.path_new, self.path_cur):
                for name in os.listdir(path):
                    fullpath = join(path, name)
                    if isfile(fullpath):
                        msgkey, _, info = name.partition(':')
                        toc[msgkey] = fullpath, info

            self._toc = toc
            return toc

    def _make_tmp_file(self):
        now = time()
        self._counter += 1
        prefix = '{}.Q{}P{}'.format(int(now), self._counter, self._pid)
        suffix = '.{}'.format(self._host)
        return mkstemp(suffix, prefix, self.path_tmp)

    def add(self, message, flags=''):
        with self.lock:
            fd, fpath = self._make_tmp_file()
            msgkey = basename(fpath)

            if isinstance(message, Message):
                message = message.as_bytes()

            os.write(fd, message)
            os.close(fd)

            newpath, info = self._get_path(msgkey, flags)
            os.link(fpath, newpath)
            os.unlink(fpath)

            self.toc[msgkey] = newpath, info

        return msgkey

    def _invalidate(self):
        with self.lock:
            try:
                del self._toc
            except AttributeError:
                pass

    def get_flags(self, key):
        _, info = self.toc[key]
        return parse_info(info)

    def discard(self, key):
        with self.lock:
            try:
                path, _ = self.toc[key]
            except KeyError:
                return

            try:
                os.remove(path)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    raise

            self.toc.pop(key, None)

    def _get_path(self, key, flags):
        if flags:
            info = ':2,' + flags
        else:
            info = ''

        if 'S' in flags:
            store_path = self.path_cur
        else:
            store_path = self.path_new

        return join(store_path, key + info), info.lstrip(':')

    def _set_flags(self, key, flags):
        oldpath, _ = self.toc[key]
        newpath, info = self._get_path(key, flags)
        os.rename(oldpath, newpath)
        self.toc[key] = newpath, info

    def add_flags(self, key, flags):
        with self.lock:
            oldflags = self.get_flags(key)
            added = set(flags) - set(oldflags)
            if added:
                newflags = oldflags + ''.join(added)
                self._set_flags(key, newflags)

    def set_flags(self, key, flags):
        with self.lock:
            oldflags = set(self.get_flags(key))
            if set(flags) != oldflags:
                self._set_flags(key, flags)

    def iterflags(self):
        for key, (_, info) in self.toc.items():
            yield key, parse_info(info)

    def __contains__(self, key):
        return key in self.toc

    def __getitem__(self, key):
        path, info = self.toc[key]
        msg = MaildirMessage(open(path).read())
        msg.set_flags(parse_info(info))
        msg.msgkey = key
        return msg
