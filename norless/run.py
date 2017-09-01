import sys
import ssl
import socket
import os.path
import argparse
import threading
import logging

from collections import Counter

from mailbox import MaildirMessage
from email.utils import parseaddr

from .utils import FileLock
from .maildir import Maildir
from .config import IniConfig
from .state import DBMStateFactory

get_maildir_lock = threading.Lock()
log = logging.getLogger('norless')


def error(msg=None):
    if msg:
        print >>sys.stderr, msg

    sys.exit(1)


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


def store_message(maildir, state, uid, message, flags):
    msg = MaildirMessage(message)
    if '\\Seen' in flags:
        msg.add_flag('S')

    flags = msg.get_flags()

    uid = int(uid)
    s = state.get(uid)
    if s:
        if s.flags != flags:
            maildir.set_flags(s.msgkey, flags)
    else:
        key = maildir.add(msg, flags)
        state.put(uid, key, flags)


def get_maildir_changes(maildir, state):
    seen = []
    trash = []
    for row in state.getall():
        flags = set(row.flags)

        try:
            mflags = maildir.get_flags(row.msgkey)
        except KeyError:
            trash.append(row.uid)
        else:
            if 'S' in mflags and 'S' not in flags:
                seen.append(row.uid)

    return seen, trash


def sync_account(config, sync_list):
    try:
        for s in sync_list:
            account = config.accounts[s.account]
            maildir = get_maildir(s.maildir)
            state = config.get_state(s.account, s.folder)

            maxuid = state.get_maxuid()
            folder = account.get_folder(s.folder)
            messages = folder.fetch(config.fetch_last, maxuid)

            for m in messages:
                store_message(maildir, state, m['uid'], m['body'], m['flags'])

            if not s.maildir.sync_new:
                new_messages = [r for r in state.getall() if not r.flags]
                messages_to_check = []

                for m in new_messages:
                    if m.msgkey in maildir:
                        messages_to_check.append(m)

                if messages_to_check:
                    flags = folder.get_flags([r.uid for r in messages_to_check])
                    for m in messages_to_check:
                        if m.uid not in flags:
                            maildir.discard(m.msgkey)
                            state.remove(m.uid)
                        elif '\\Seen' in flags[m.uid]:
                            maildir.add_flags(m.msgkey, 'S')
                            state.put(m.uid, m.msgkey, maildir.get_flags(m.msgkey))
    except:
        log.exception('Error during processing account %s %s', s.account, s.folder)


def remote_sync_account(config, sync_list):
    for sr in sync_list:
        account = config.accounts[sr.account]
        maildir = get_maildir(sr.maildir)
        state = config.get_state(sr.account, sr.folder)

        seen, trash = get_maildir_changes(maildir, state)
        if seen:
            folder = account.get_folder(sr.folder)
            folder.seen(seen)
            for uid in seen:
                s = state.get(uid)
                if s:
                    flags = set(s.flags)
                    flags.add('S')
                    flags = ''.join(flags)
                    state.put(s.uid, s.msgkey, flags, s.is_check)

        if trash:
            folder = account.get_folder(sr.folder)
            folder.trash(trash, sr.trash)
            state.remove_many(trash)

        if (seen or trash) and not config.quiet:
            print '{}: seen {}, trash {}'.format(sr.account,
                len(seen), len(trash))


def do_remote_sync(config):
    with config.app_lock(True):
        accounts = {}
        for s in config.sync_list:
            accounts.setdefault(s.account, []).append(s)

        for sync_list in accounts.itervalues():
            remote_sync_account(config, sync_list)


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
                    sm = folder.append_messages(messages, state.get_maxuid())
                    for uid, msgkey in sm:
                        state.put(uid, msgkey, 'S')


def do_check(config):
    maildirs = set(s.maildir for s in config.sync_list)

    result = Counter()
    for cmaildir in maildirs:
        maildir = get_maildir(cmaildir)
        for _, flags in maildir.iterflags():
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
            if '&' in name:
                lname = ' ({})'.format(name.replace('&', '+').replace(',', '/')
                    .decode('utf-7').encode('utf-8'))
            else:
                lname = ''

            print '   [{}] {}\t({}){}'.format(s, name, f, lname)


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
commands to get certificates:
    openssl s_client -showcerts -connect host:993 < /dev/null
    openssl s_client -showcerts -starttls imap -connect host:143 < /dev/null'''
    )
    parser.add_argument('-S', '--sync', dest='do_sync', action='store_true',
        help='command: sync remote folders to local maildir(s)')

    parser.add_argument('-R', '--remote-sync', dest='do_remote_sync', action='store_true',
        help='command: sync local changes to remote maildirs')

    parser.add_argument('-C', '--check', dest='do_check', action='store_true',
        help='command: check for new messages in local maildir(s)')

    parser.add_argument('-N', '--new', dest='do_new', action='store_true',
        help='command: sync new messages in maildir(s)')

    parser.add_argument('--show-folders', dest='do_show_folders', action='store_true',
        help='command: list remote folders')

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

    logging.basicConfig(level='ERROR')

    commands = []
    for action in sorted(parser._actions,
            key=lambda r: min(get_index(opt) for opt in r.option_strings)):
        if action.dest.startswith('do_') and getattr(args, action.dest):
            commands.append(action.dest)

    for command in commands:
        globals()[command](config)
