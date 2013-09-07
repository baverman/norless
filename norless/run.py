import sys
import json
import socket
import os.path
import argparse
import threading

from collections import Counter

from mailbox import MaildirMessage
from email.utils import parseaddr

from .utils import FileLock
from .maildir import Maildir
from .config import IniConfig
from .state import DBMStateFactory

get_maildir_lock = threading.Lock()

maildir_cache = {}
def get_maildir(maildir):
    with get_maildir_lock:
        key = os.path.expanduser(maildir.path)
        try:
            return maildir_cache[key]
        except KeyError:
            pass

        result = maildir_cache[key] = Maildir(key)
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
                if s.msgkey in maildir:
                    newflags = maildir.add_flags(s.msgkey, 'S')
                    state.put(s.uid, s.msgkey, newflags)
                else:
                    state.remove(uid)

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
            changes = json.loads(msg.get_payload(decode=True))
            apply_remote_changes(maildir, state, changes, uid)
            return

    if '\\Seen' in flags:
        msg.add_flag('S')

    flags = msg.get_flags()

    s = state.get(uid)
    if s:
        if s.flags != flags:
            maildir.set_flags(s.msgkey, flags)
    else:
        key = maildir.add(msg, flags)
        state.put(uid, key, flags)

def get_maildir_changes(maildir, state):
    changes = {'seen':[], 'trash':[]}
    for row in state.getall():  
        if row.is_check:
            continue

        flags = set(row.flags)

        try:
            mflags = maildir.get_flags(row.msgkey)
        except KeyError:
            changes['trash'].append(row.uid)
        else:
            if 'S' in mflags and 'S' not in flags:
                changes['seen'].append(row.uid)

    return changes

def sync_account(config, sync_list):
    for s in sync_list:
        account = config.accounts[s.account] 
        maildir = get_maildir(s.maildir)
        state = config.get_state(s.account, s.folder)

        maxuid = state.get_maxuid()
        skip_syncpoints = not maxuid

        folder = account.get_folder(s.folder)
        messages = folder.fetch(config.fetch_last, maxuid)
        for m in messages: 
            store_message(config, maildir, state, skip_syncpoints,
                m['uid'], m['body'], m['flags'])

def checkpoint_account(config, sync_list):
    for s in sync_list:
        account = config.accounts[s.account] 
        maildir = get_maildir(s.maildir)
        state = config.get_state(s.account, s.folder)

        changes = get_maildir_changes(maildir, state)
        if changes['trash'] or changes['seen']:
            folder = account.get_folder(s.folder)
            folder.apply_changes(config, changes, state, s.trash)
            if not config.quiet:
                print '{}: seen {}, trash {}'.format(s.account,
                    len(changes['seen']), len(changes['trash']))

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

def do_new(config):
    with config.app_lock():
        maildirs = {}
        for s in config.sync_list:
            if s.maildir.sync_new:
                maildirs.setdefault(s.maildir.name, []).append(s)

        for sync_list in maildirs.itervalues():
            maildir = get_maildir(sync_list[0].maildir)
            state_keys = set()
            for s in sync_list:
                state = config.get_state(s.account, s.folder)
                state_keys.update(r.msgkey for r in state.getall())
            
            maildir_keys = set(maildir.toc)
            new_messages = maildir_keys - state_keys

            if new_messages:
                addr_messages = {}
                for msgkey in new_messages:
                    msg = maildir[msgkey]
                    addr = parseaddr(msg['From'])[1]
                    addr_messages.setdefault(addr, []).append(msg)

                for addr, messages in addr_messages.iteritems():
                    for s in sync_list:
                        account = config.accounts[s.account]
                        if account.from_addr == addr:
                            break
                    else:
                        state = config.get_state(s.account, s.folder)
                        minuid = state.get_minuid()
                        for r in messages:
                            minuid -= 1
                            state.put(minuid, r.msgkey, 'S')

                        print >>sys.stderr, 'Unknown addr', addr
                        continue

                    folder = account.get_folder(s.folder)
                    state = config.get_state(s.account, s.folder)
                    folder.append_messages(state, messages)

def do_check(config):
    maildirs = set(s.maildir for s in config.sync_list)

    result = Counter()
    for cmaildir in maildirs:
        maildir = get_maildir(cmaildir)
        for key, flags in maildir.iterflags():
            if 'S' not in flags:
                result[cmaildir.name] += 1

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-S', '--sync', dest='do_sync', action='store_true',
        help='command: sync remote folders to local maildir(s)')

    parser.add_argument('-P', '--checkpoint', dest='do_checkpoint', action='store_true',
        help='command: make checkpoint for local changes')

    parser.add_argument('-C', '--check', dest='do_check', action='store_true',
        help='command: check for new messages in local maildir(s)')

    parser.add_argument('-N', '--new', dest='do_new', action='store_true',
        help='command: sync new messages in maildir(s)')

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

    parser.add_argument('-q', '--quiet', dest='quiet', action='store_true',
        help='silent run')

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
    config.quiet = args.quiet

    if config.timeout:
        socket.setdefaulttimeout(config.timeout)

    config.app_lock = FileLock(os.path.join(os.path.dirname(
        os.path.expanduser(config.state_dir)), '.norless-lock'))

    dbm_state = DBMStateFactory(os.path.expanduser(config.state_dir))
    config.get_state = lambda a, f: dbm_state.get(a, f)

    commands = []
    for action in sorted(parser._actions,
            key=lambda r: min(get_index(opt) for opt in r.option_strings)):
        if action.dest.startswith('do_') and getattr(args, action.dest):
            commands.append(action.dest)
    
    for command in commands:
        globals()[command](config)
