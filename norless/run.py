import sys
import json
import fcntl
import os.path
import argparse
import threading

from mailbox import Maildir, MaildirMessage
from collections import Counter

from .config import IniConfig
from .state import State, connect, create_tables

get_maildir_lock = threading.Lock()

class ConcurentMaildir(Maildir):
    def __init__(self, *args, **kwargs):
        Maildir.__init__(self, *args, **kwargs)
        self.refresh_lock = threading.Lock()
        self.store_lock = threading.Lock()

    def _refresh(self):
        with self.refresh_lock:
            return Maildir._refresh(self)

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
            print s
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

def sync_local(maildir, state):
    maxuid = 0
    changes = {'seen':[], 'trash':[]}
    for row in state.getall():  
        maxuid = max(row.uid, maxuid)
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

    return maxuid, changes

def sync_account(config, sync_list):
    conn = connect(os.path.expanduser(config.state_db))
    for s in sync_list:
        account = config.accounts[s.account] 
        maildir = get_maildir(s.maildir)
        state = State(conn, s.account, s.folder)

        maxuid, changes = sync_local(maildir, state)
        skip_syncpoints = not maxuid

        folder = account.get_folder(s.folder)
        folder.apply_changes(config, changes, state, s.trash)

        messages = folder.fetch(config.fetch_last, maxuid)
        for m in messages: 
            store_message(config, maildir, state, skip_syncpoints,
                m['uid'], m['body'], m['flags'])

def sync(config):
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

def check(config):
    maildirs = set(s.maildir for s in config.sync_list)

    result = Counter()
    for maildir_path in maildirs:
        maildir = get_maildir(maildir_path)
        for key in maildir.iterkeys():
            if 'S' not in maildir.cm_get_flags(key):
                result[maildir.name] += 1

    for k, v in result.iteritems():
        print '{}\t{}'.format(k, v)

    return result

def show_folders(config):
    for account, box in config.accounts.iteritems():
        print account
        for f, s, name in box.list_folders():
            print '   [{}] {}\t({})'.format(s, name, f)

def show_fingerprint(config):
    for account, box in config.accounts.iteritems():
        box.fingerprint = None
        print account, box.server_fingerprint

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-C', '--config', dest='config',
        default=os.path.expanduser('~/.config/norlessrc'))

    parser.add_argument('-c', '--check', dest='check', action='store_true')
    parser.add_argument('-s', '--show-folders', dest='show_folders', action='store_true')
    parser.add_argument('-a', '--account', dest='account')
    parser.add_argument('--init-state', dest='init_state', action='store_true')
    parser.add_argument('--show-fingerprint', dest='show_fingerprint', action='store_true')
    parser.add_argument('-T', '--one-thread', dest='one_thread', action='store_true')
    
    args = parser.parse_args()

    config = IniConfig(args.config)
    if args.account:
        config.restrict_to(args.account)

    config.one_thread = args.one_thread

    if args.show_folders:
        show_folders(config)
    elif args.show_fingerprint:
        show_fingerprint(config)
    elif args.init_state:
        with connect(os.path.expanduser(config.state_db)) as conn:
            create_tables(conn)
    else:
        lock_file = os.path.join(
            os.path.dirname(os.path.expanduser(config.state_db)), '.norless-lock')
        fp = open(lock_file, 'w')
        try:
            fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            print >>sys.stderr, 'Another instance already running'
            sys.exit(1)

        sync(config)
        if args.check:
            if not check(config):
                sys.exit(1)

