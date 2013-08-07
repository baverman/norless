import sys
import json
import socket
import os.path
import argparse
import threading

from collections import Counter
from mailbox import Maildir, MaildirMessage

from .utils import FileLock
from .config import IniConfig
from .state import State, connect, create_tables

get_maildir_lock = threading.Lock()
state_write_lock = threading.Lock()

class ConcurentMaildir(Maildir):
    def __init__(self, *args, **kwargs):
        Maildir.__init__(self, *args, **kwargs)
        self.refresh_lock = threading.Lock()
        self.store_lock = threading.Lock()
        self.refreshed = False

    def _refresh(self, force=False):
        if not force and self.refreshed:
            return

        with self.refresh_lock:
            if not force and self.refreshed:
                return

            self._toc = {}
            for subdir in self._toc_mtimes:
                path = self._paths[subdir]
                for entry in os.listdir(path):
                    p = os.path.join(path, entry)
                    if os.path.isdir(p):
                        continue
                    uniq = entry.split(self.colon)[0]
                    self._toc[uniq] = os.path.join(subdir, entry)

            self.refreshed = True

    def _lookup(self, key):
        try:
            if os.path.exists(os.path.join(self._path, self._toc[key])):
                return self._toc[key]
        except KeyError:
            pass
        self._refresh(True)
        try:
            return self._toc[key]
        except KeyError:
            raise KeyError('No message with key: %s' % key)

    def cm_get_flags(self, key):
        mpath = self._lookup(key)
        name = os.path.basename(mpath)
        _, sep, info = name.rpartition(':')
        if sep:
            _, sep, flags = info.rpartition(',')
            if sep:
                return flags
        
        return ''


maildir_cache = {}
def get_maildir(maildir):
    with get_maildir_lock:
        key = os.path.expanduser(maildir)
        try:
            return maildir_cache[key]
        except KeyError:
            pass

        result = maildir_cache[key] = ConcurentMaildir(key, factory=None, create=True)
        result.name = os.path.basename(maildir)
        return result

def apply_remote_changes(maildir, state, changes, change_uid):
    uids = changes['trash']
    if uids:
        for uid in uids:
            s = state.get(uid)
            if s and not s.is_check:
                maildir.discard(s.msgkey)
                state.remove(uid)

    uids = changes['seen']
    if uids:
        for uid in uids:
            s = state.get(uid)
            if s and not s.is_check:
                try:
                    msg = maildir[s.msgkey] 
                except KeyError:
                    state.remove(uid)
                else:
                    msg.add_flag('S')
                    with maildir.store_lock:
                        maildir[s.msgkey] = msg

                    state.put(s.uid, s.msgkey, msg.get_flags())

    state.put(change_uid, '', 'S', 1)

def store_message(config, maildir, state, skip_syncpoints, uid, message, flags):
    uid = int(uid)

    msg = MaildirMessage(message)
    if 'X-Norless' in msg:
        replica_id = msg['X-Norless']
        if skip_syncpoints or replica_id == config.replica_id:
            state.put(uid, '', 'S', 1)
            return
        else:
            changes = json.loads(msg.get_payload())
            apply_remote_changes(maildir, state, changes, uid)
            return

    if '\\Seen' in flags:
        msg.add_flag('S')

    s = state.get(uid)
    if s:
        if s.flags != msg.get_flags():
            oldmessage = maildir[s.msgkey]
            oldmessage.set_flags(msg.get_flags())
            with maildir.store_lock:
                maildir[s.msgkey] = oldmessage
    else:
        with maildir.store_lock:
            key = maildir.add(msg)

        state.put(uid, key, msg.get_flags())

def get_maildir_changes(maildir, state):
    changes = {'seen':[], 'trash':[]}
    for row in state.getall():  
        if row.is_check:
            continue

        flags = set(row.flags)

        try:
            mflags = maildir.cm_get_flags(row.msgkey)
        except KeyError:
            changes['trash'].append(row.uid)
        else:
            if 'S' in mflags and 'S' not in flags:
                changes['seen'].append(row.uid)

    return changes

def sync_account(config, sync_list):
    with config.connect() as conn:
        for s in sync_list:
            account = config.accounts[s.account] 
            maildir = get_maildir(s.maildir)
            state = State(conn, s.account, s.folder, state_write_lock)

            maxuid = 0
            for row in state.getall():
                maxuid = max(maxuid, row.uid)
            skip_syncpoints = not maxuid

            folder = account.get_folder(s.folder)
            messages = folder.fetch(config.fetch_last, maxuid)
            for m in messages: 
                store_message(config, maildir, state, skip_syncpoints,
                    m['uid'], m['body'], m['flags'])

def checkpoint_account(config, sync_list):
    with config.connect() as conn:
        for s in sync_list:
            account = config.accounts[s.account] 
            maildir = get_maildir(s.maildir)
            state = State(conn, s.account, s.folder, state_write_lock)

            changes = get_maildir_changes(maildir, state)
            if changes['trash'] or changes['seen']:
                folder = account.get_folder(s.folder)
                folder.apply_changes(config, changes, state, s.trash)
                print 'seen: {}, trash: {}'.format(len(changes['seen']), len(changes['trash']))

def do_checkpoint(config):
    with config.app_lock(True):
        accounts = {}
        for s in config.sync_list:
            accounts.setdefault(s.account, []).append(s)

        for sync_list in accounts.itervalues():
            checkpoint_account(config, sync_list)

def do_sync(config):
    with config.app_lock():
        accounts = {}
        for s in config.sync_list:
            accounts.setdefault(s.account, []).append(s)

        if config.one_thread:
            for sync_list in accounts.itervalues():
                sync_account(config, sync_list)
        else:
            threads = []
            for sync_list in accounts.itervalues():
                t = threading.Thread(target=sync_account,
                    args=(config, sync_list))

                t.start()
                threads.append(t)
            
            for t in threads:
                t.join()

def do_check(config):
    maildirs = set(s.maildir for s in config.sync_list)

    result = Counter()
    for maildir_path in maildirs:
        maildir = get_maildir(maildir_path)
        maildir._refresh(True)
        for key in maildir.iterkeys():
            if 'S' not in maildir.cm_get_flags(key):
                result[maildir.name] += 1

    for k, v in result.iteritems():
        print '{}\t{}'.format(k, v)

    if not result:
        sys.exit(1)

def do_show_folders(config):
    for account, box in config.accounts.iteritems():
        print account
        for f, s, name in box.list_folders():
            print '   [{}] {}\t({})'.format(s, name, f)

def do_show_fingerprint(config):
    for account, box in config.accounts.iteritems():
        box.fingerprint = None
        print account, box.server_fingerprint

def do_init_state(config):
    with config.connect() as conn:
        create_tables(conn)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--init-state', dest='do_init_state', action='store_true',
        help='command: initialize state db')

    parser.add_argument('-S', '--sync', dest='do_sync', action='store_true',
        help='command: sync remote folders to local maildir(s)')

    parser.add_argument('-P', '--checkpoint', dest='do_checkpoint', action='store_true',
        help='command: make checkpoint for local changes')

    parser.add_argument('-C', '--check', dest='do_check', action='store_true',
        help='command: check for new messages in local maildir(s)')

    parser.add_argument('--show-folders', dest='do_show_folders', action='store_true',
        help='command: list remote folders')

    parser.add_argument('--show-fingerprint', dest='do_show_fingerprint',
        action='store_true', help='command: show server cert fingerprint')

    parser.add_argument('-f', '--config', dest='config',
        default=os.path.expanduser('~/.config/norlessrc'),
        help='path to config file (%(default)s)')

    parser.add_argument('-a', '--account', dest='account',
        help='process this account only')

    parser.add_argument('-s', '--run-sequentially', dest='one_thread', action='store_true',
        help='run actions sequentially in one thread')

    def get_index(r):
        try:
            return sys.argv.index(r)
        except ValueError:
            return 9999

    args = parser.parse_args()

    config = IniConfig(args.config)
    if args.account:
        config.restrict_to(args.account)

    config.one_thread = args.one_thread

    if config.timeout:
        socket.setdefaulttimeout(config.timeout)

    config.app_lock = FileLock(os.path.join(os.path.dirname(
        os.path.expanduser(config.state_db)), '.norless-lock'))

    config.connect = lambda: connect(os.path.expanduser(config.state_db))

    commands = []
    for action in sorted(parser._actions,
            key=lambda r: min(get_index(opt) for opt in r.option_strings)):
        if action.dest.startswith('do_') and getattr(args, action.dest):
            commands.append(action.dest)
    
    for command in commands:
        globals()[command](config)
