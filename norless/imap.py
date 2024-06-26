import re
import time
import imaplib
import ssl

from hashlib import sha1
from mailbox import Message

from .utils import cached_property, check_cert, bstr, nstr

LIST_REGEX = re.compile(rb'\((?P<flags>.*?)\) "(?P<sep>.*)" (?P<name>.*)')


def get_field(info, field):
    field = bstr(field)
    idx = info.index(field + b' ')
    return nstr(info[idx + len(field):].split()[0].strip(b')'))


class ImapBox(object):
    def __init__(self, host, username, password, port=None, ssl=True,
            fingerprint=None, cafile=None, debug=None, xoauth2=None):
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

    def get_cert(self, client):
        if self.ssl:
            return client.sock.getpeercert(True)

    def get_fingerprint(self, cert):
        return ':'.join(map(lambda r: r.encode('hex').upper(),
            sha1(cert).digest()))

    @cached_property
    def client(self):
        C = imaplib.IMAP4_SSL if self.ssl else imaplib.IMAP4
        cl = C(self.host, self.port)

        if self.ssl:
            if self.fingerprint:
                server_fingerprint = self.get_fingerprint(self.get_cert(cl))
                if server_fingerprint != self.fingerprint:
                    raise Exception('Mismatched fingerprint for {} {}'.format(
                        self.host, server_fingerprint))
            elif self.cafile:
                cert = ssl.DER_cert_to_PEM_cert(self.get_cert(cl))
                check_cert(bstr(cert), self.cafile)

        if self.debug:
            cl.debug = self.debug

        if self.xoauth2:
            self.xoauth2_login(cl, self.xoauth2, self.username)
        else:
            cl.login(self.username, self.password)
        return cl

    def xoauth2_login(self, client, cfg, username):
        def xoauth(data):
            return 'user={}\x01auth=Bearer {}\x01\x01'.format(username, self.xoauth2.get_token())
        client.authenticate('XOAUTH2', xoauth)

    def list_folders(self):
        result = []
        resp = self.client.list()
        for item in resp[1]:
            m = LIST_REGEX.search(item)
            flags, sep, name = m.group('flags', 'sep', 'name')
            name = nstr(name).strip('"')
            result.append((nstr(flags), nstr(sep), name))

        return result

    def get_folder(self, name):
        return Folder(self, name)

    def get_status(self, folder):
        result = self.client.status(self.client._quote(folder), '(MESSAGES UNSEEN)')
        messages = int(get_field(result[1][0], 'MESSAGES'))
        unseen = int(get_field(result[1][0], 'UNSEEN'))
        return messages, unseen

    def select(self, name):
        if name != self.selected_folder:
            self.client.select(self.client._quote(name))
            self.selected_folder = name


class Folder(object):
    def __init__(self, box, name):
        self.box = box
        self.name = name

        self._total = None
        self._new = None

    @property
    def total(self):
        if self._total is None:
            self.refresh()
        return self._total

    @property
    def new(self):
        if self._new is None:
            self.refresh()
        return self._new

    def refresh(self):
        self._total, self._new = self.box.get_status(self.name)

    def select(self):
        self.box.select(self.name)

    def trash(self, uids, trash_folder):
        uids = ','.join(map(str, uids))
        self.select()
        self.box.client.uid('COPY', uids, trash_folder)
        self.box.client.uid('STORE', uids, '+FLAGS', '(\\Deleted)')
        self.box.client.expunge()

    def seen(self, uids):
        uids = ','.join(map(str, uids))
        self.select()
        self.box.client.uid('STORE', uids, '+FLAGS', '(\\Seen)')

    def fetch(self, last_n=None, last_uid=None):
        self.select()
        result_messages = []

        if last_uid:
            result = self.box.client.uid('search', '(UID {}:*)'.format(last_uid+1))
            uids = [r for r in result[1][0].split() if int(r) > last_uid]
            if not uids:
                return []

            result = self.box.client.uid('fetch', b','.join(uids),
                '(UID FLAGS BODY.PEEK[])')
        else:
            if not self.total:
                return []

            start, end = max(self.total - last_n, 1), self.total
            result = self.box.client.fetch('{}:{}'.format(start, end), '(UID FLAGS BODY.PEEK[])')

        it = iter(result[1])
        for info, msg in it:
            r = {}
            r['uid'] = get_field(info, 'UID')
            r['flags'] = tuple(map(nstr, imaplib.ParseFlags(info)))
            r['body'] = msg.replace(b'\r\n', b'\n')
            result_messages.append(r)
            next(it)

        return result_messages

    def get_flags(self, uids):
        result = self.box.client.uid('fetch', ','.join(map(str, uids)), '(UID FLAGS)')
        flags = {}
        for info in result[1]:
            if not info: continue
            flags[int(get_field(info, 'UID'))] = tuple(map(nstr, imaplib.ParseFlags(info)))

        return flags

    def append_messages(self, messages, last_uid):
        self.select()
        for msg in messages:
            del msg['X-Norless-Id']
            msg['X-Norless-Id'] = msg.msgkey

            del msg['Message-ID']
            msg['Message-ID'] = msg.msgkey

            self.box.client.append(self.box.client._quote(self.name), '(\\Seen)', time.time(), msg.as_bytes())

        result = self.box.client.uid('search', '(UID {}:*)'.format(last_uid + 1))

        uids = [r for r in result[1][0].split() if int(r) > last_uid]
        stored_messages = []
        if uids:
            result = self.box.client.uid('fetch', b','.join(uids),
                '(UID BODY.PEEK[HEADER])')

            it = iter(result[1])
            for info, msg in it:
                uid = get_field(info, 'UID')
                msg = Message(msg.replace(b'\r\n', b'\n'))

                if 'X-Norless-Id' in msg:
                    stored_messages.append((int(uid), msg['X-Norless-Id'].strip()))

                if 'Message-ID' in msg:
                    stored_messages.append((int(uid), msg['Message-ID'].strip()))

                next(it)

        return stored_messages
