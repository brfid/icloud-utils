#!/usr/bin/env python3
"""Read iCloud Mail through Himalaya and keep review acknowledgments locally.

Himalaya owns protocols, MIME, and credentials. This adapter constructs only
allowlisted read operations; it never forwards arbitrary backend arguments.
"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib

VERSION = '1.0.1'
SUPPORTED_HIMALAYA_VERSIONS = ('2.1.0', '2.2.1')
DEFAULT_CONFIG = Path.home() / '.config/icloud-utils/mail.toml'


class Failure(Exception):
    """A bounded operation failed without establishing review coverage."""

    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def now():
    return dt.datetime.now(dt.UTC).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def label(value):
    """Routing and identity labels cannot contain empty text or control bytes."""
    return isinstance(value, str) and bool(value.strip()) and not re.search(r'[\x00-\x1f\x7f]', value)


def calendar_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('use YYYY-MM-DD')
    return dt.date.fromisoformat(value)


def timestamp(value):
    if not isinstance(value, str) or not re.match(r'^\d{4}-\d{2}-\d{2}T', value):
        raise ValueError('use a timestamp with a timezone')
    parsed = dt.datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError('use a timestamp with a timezone')
    return parsed


def documentation():
    root = Path(__file__).resolve().parent
    if (root / '.icloud-utils-mail').is_file():
        root = root.parent.parent / 'documentation'
    return root / 'docs/mail.md'


def positive_uid(value):
    if not re.fullmatch(r'[1-9][0-9]{0,9}', str(value)) or int(value) > 4294967295:
        raise argparse.ArgumentTypeError('use a single positive IMAP UID or MIME part ID')
    return str(value)


def bounded_number(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('use an integer from 1 through 100') from None
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError('use an integer from 1 through 100')
    return number


@dataclass(frozen=True)
class Config:
    path: Path
    account: str
    himalaya: str
    himalaya_config: Path
    state_dir: Path
    timeout: float

    @classmethod
    def load(cls, path):
        try:
            path = Path(path).expanduser().resolve()
            data = tomllib.loads(path.read_text())
            if set(data) != {'mail'} or not isinstance(data['mail'], dict):
                raise ValueError('only the [mail] table is supported')
            mail = data['mail']
            allowed = {'account', 'himalaya', 'himalaya_config', 'state_dir', 'timeout_seconds'}
            if set(mail) - allowed:
                raise ValueError('unknown mail settings; credentials belong in Himalaya/Keychain')
            for key in ('account', 'himalaya_config'):
                if not label(mail.get(key)):
                    raise ValueError(f'{key} must be a nonempty string without control characters')
            binary = mail.get('himalaya', 'himalaya')
            if not label(binary):
                raise ValueError('himalaya must name one executable, not a shell command')
            timeout = mail.get('timeout_seconds', 30)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 300:
                raise ValueError('timeout_seconds must be from 1 through 300')
            state_value = mail.get('state_dir', '~/Library/Application Support/icloud-utils/mail/state')
            if not label(state_value):
                raise ValueError('state_dir must be a nonempty path string without control characters')
            backend_path = Path(mail['himalaya_config']).expanduser()
            state = Path(state_value).expanduser()
            if not backend_path.is_absolute() or not state.is_absolute():
                raise ValueError('himalaya_config and state_dir must be absolute or start with ~/')
            return cls(path, mail['account'], binary, backend_path.resolve(), state.resolve(), float(timeout))
        except tomllib.TOMLDecodeError:
            raise Failure('configuration', f'Toolkit mail configuration at {path} is not valid TOML; its contents were withheld.') from None
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            raise Failure('configuration', f'Cannot load toolkit mail configuration at {path}: {error}') from None

    def account_settings(self):
        """Inspect routing/identity only; never execute or copy a credential command."""
        try:
            data = tomllib.loads(self.himalaya_config.read_text())
            if not isinstance(data.get('accounts'), dict):
                raise ValueError()
            account = data['accounts'][self.account]
            if not isinstance(account, dict) or not isinstance(account.get('imap'), dict):
                raise ValueError()
            imap = account['imap']
            if not isinstance(imap.get('sasl'), dict) or not isinstance(imap['sasl'].get('plain'), dict):
                raise ValueError()
            server = imap['server']
            username = imap['sasl']['plain']['username']
            if not label(server) or not label(username):
                raise ValueError('IMAP server and login must be nonempty strings')
            if not isinstance(imap.get('sort'), dict) or imap['sort'].get('fallback') is not True:
                raise ValueError('iCloud requires imap.sort.fallback = true for reliable ordering')
            mailboxes = account.get('mailbox', {})
            if not isinstance(mailboxes, dict) or not isinstance(mailboxes.get('alias', {}), dict):
                raise ValueError('mailbox aliases must be a table')
            aliases = mailboxes.get('alias', {})
            if any(not label(key) or not label(value) for key, value in aliases.items()):
                raise ValueError('mailbox aliases must contain nonempty names')
            if len({key.casefold() for key in aliases}) != len(aliases):
                raise ValueError('mailbox aliases must be distinct ignoring case')
            return account
        except (OSError, KeyError, TypeError, ValueError):
            raise Failure('configuration', f'Himalaya must define the selected IMAP account, plain login, local sort fallback, and valid mailbox aliases; see {documentation()}.') from None

    def scope(self):
        account = self.account_settings()
        imap = account['imap']
        return digest([imap['server'].casefold(), imap['sasl']['plain']['username'].casefold()])

    def mailbox(self, name):
        if not label(name):
            raise Failure('usage', 'Use one nonempty mailbox name.')
        aliases = self.account_settings().get('mailbox', {}).get('alias', {})
        resolved = next((value for key, value in aliases.items() if key.casefold() == name.casefold()), name)
        if not label(resolved):
            raise Failure('configuration', 'The selected mailbox alias is invalid.')
        return 'INBOX' if resolved.casefold() == 'inbox' else resolved


class Backend:
    """Run known Himalaya commands in bounded child process groups."""

    def __init__(self, config):
        self.config = config
        configured = os.path.expanduser(config.himalaya)
        self.binary = shutil.which(configured)
        if not self.binary:
            raise Failure('dependency', 'Himalaya is missing; install the supported release or set mail.himalaya.')
        version = self.process(['--version']).stdout.strip()
        match = re.match(r'himalaya v?(\d+\.\d+\.\d+)(?:\s|$)', version)
        if not match or match.group(1) not in SUPPORTED_HIMALAYA_VERSIONS:
            supported = ', '.join(SUPPORTED_HIMALAYA_VERSIONS)
            raise Failure('compatibility', f'This mail adapter supports Himalaya {supported}; test other releases before updating the supported versions.')
        self.version = match.group(1)

    def process(self, arguments):
        environment = dict(os.environ)
        environment.pop('RUST_LOG', None)
        child = subprocess.Popen([self.binary, *arguments], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=environment, start_new_session=True)
        try:
            stdout, stderr = child.communicate(timeout=self.config.timeout)
        except BaseException as error:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.communicate()
            if isinstance(error, subprocess.TimeoutExpired):
                raise Failure('timeout', 'Himalaya was stopped before completion; no review was acknowledged.') from None
            raise
        if child.returncode:
            # Backend streams may contain private account/message data. Never echo
            # them in a wrapper failure, and never infer success from partial JSON.
            raise Failure('backend', f'Himalaya failed with exit {child.returncode}; review coverage was not advanced. Diagnose locally with Himalaya.')
        return subprocess.CompletedProcess([self.binary, *arguments], child.returncode, stdout, stderr)

    def call(self, arguments):
        command = ['--json', '--log-level', 'off', '--config', str(self.config.himalaya_config),
                   '--account', self.config.account, '--backend', 'imap', *arguments]
        result = self.process(command)
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise Failure('protocol', 'Himalaya returned invalid JSON; backend output was withheld and no review was acknowledged.') from None
        if not isinstance(data, dict) or 'error' in data:
            raise Failure('protocol', 'Himalaya returned an unexpected result or embedded error; no review was acknowledged.')
        return data

    def epoch(self, mailbox):
        data = self.call(['imap', 'status', '--', mailbox])
        value = data.get('uid_validity')
        if type(value) is not int or not 0 < value <= 4294967295:
            raise Failure('identity', 'The mailbox did not return a usable UIDVALIDITY; review identity cannot be established.')
        return value


def table(data, key):
    rows = data.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise Failure('protocol', f'Himalaya returned an invalid {key} result.')
    return rows


class Ledger:
    """Store receipts and explicit review outcomes, never message bodies/passwords."""

    def __init__(self, directory):
        self.directory = directory
        self.path = directory / 'reviews.json'

    def load(self):
        try:
            if self.directory.exists() and self.directory.stat().st_mode & 0o077:
                raise ValueError()
            if self.path.is_symlink():
                raise ValueError()
            if not self.path.exists():
                return {'schemaVersion': 1, 'entries': {}}
            if not self.path.is_file() or self.path.stat().st_mode & 0o077:
                raise ValueError()
            data = json.loads(self.path.read_text(), object_pairs_hook=self.unique_object)
            if not isinstance(data, dict) or set(data) != {'schemaVersion', 'entries'}:
                raise ValueError()
            if type(data['schemaVersion']) is not int or data['schemaVersion'] != 1 or not isinstance(data['entries'], dict):
                raise ValueError()
            tokens = set()
            for key, entry in data['entries'].items():
                required = {'token', 'scope', 'account', 'mailbox', 'uidValidity', 'uid', 'contentSha256', 'fetchedAt', 'reviewedAt', 'outcome', 'note', 'due', 'resolvedAt'}
                if not isinstance(key, str) or not isinstance(entry, dict) or set(entry) != required:
                    raise ValueError()
                if not all(label(entry[field]) for field in ('account', 'mailbox', 'uid')):
                    raise ValueError()
                for field, length in (('scope', 64), ('contentSha256', 64), ('token', 32)):
                    if not isinstance(entry[field], str) or not re.fullmatch(r'[0-9a-f]{' + str(length) + '}', entry[field]):
                        raise ValueError()
                if entry['token'] in tokens:
                    raise ValueError()
                tokens.add(entry['token'])
                if type(entry['uidValidity']) is not int or not 0 < entry['uidValidity'] <= 4294967295:
                    raise ValueError()
                positive_uid(entry['uid'])
                if key != self.key(entry['scope'], entry['mailbox'], entry['uidValidity'], entry['uid']):
                    raise ValueError()
                timestamp(entry['fetchedAt'])
                for field in ('reviewedAt', 'resolvedAt'):
                    if entry[field] is not None:
                        timestamp(entry[field])
                if entry['note'] is not None and (not isinstance(entry['note'], str) or not entry['note'].strip() or '\0' in entry['note']):
                    raise ValueError()
                if entry['due'] is not None:
                    calendar_date(entry['due'])
                if entry['outcome'] is None:
                    if any(entry[field] is not None for field in ('reviewedAt', 'note', 'due', 'resolvedAt')):
                        raise ValueError()
                elif entry['outcome'] in ('clear', 'needs-attention'):
                    if entry['reviewedAt'] is None:
                        raise ValueError()
                    if entry['outcome'] == 'clear' and entry['resolvedAt'] is not None:
                        raise ValueError()
                    if entry['outcome'] == 'needs-attention' and entry['note'] is None:
                        raise ValueError()
                else:
                    raise ValueError()
            return data
        except (OSError, ValueError, KeyError, TypeError, argparse.ArgumentTypeError):
            raise Failure('state', 'The private review ledger is unreadable or incompatible; it was not reset.') from None

    @staticmethod
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate ledger field')
            result[key] = value
        return result

    @contextmanager
    def transaction(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.stat().st_mode & 0o077:
            raise Failure('state', 'The configured review-state directory must be owner-only; its permissions were not changed.')
        descriptor = os.open(self.directory / 'reviews.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Failure('state', 'Another mail review is updating the ledger; retry this local operation.') from None
            data = self.load()
            yield data
            temporary_fd, temporary = tempfile.mkstemp(prefix='.reviews-', dir=self.directory)
            try:
                with os.fdopen(temporary_fd, 'w') as stream:
                    json.dump(data, stream, ensure_ascii=True, indent=2)
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        finally:
            os.close(descriptor)

    @staticmethod
    def key(scope, mailbox, epoch, uid):
        return digest([scope, mailbox, epoch, str(uid)])

    def receipt(self, scope, account, mailbox, epoch, uid, body):
        key = self.key(scope, mailbox, epoch, uid)
        content_hash = hashlib.sha256(body.encode()).hexdigest()
        with self.transaction() as data:
            entry = data['entries'].get(key)
            if entry and entry['contentSha256'] != content_hash:
                raise Failure('identity', 'Message content changed under the same IMAP identity; no new review receipt was accepted.')
            if not entry:
                entry = {'token': secrets.token_hex(16), 'scope': scope, 'account': account,
                         'mailbox': mailbox, 'uidValidity': epoch, 'uid': str(uid),
                         'contentSha256': content_hash, 'fetchedAt': now(), 'reviewedAt': None,
                         'outcome': None, 'note': None, 'due': None, 'resolvedAt': None}
                data['entries'][key] = entry
        return entry

    def update(self, scope, token, outcome=None, note=None, due=None, resolve=False):
        with self.transaction() as data:
            entry = next((entry for entry in data['entries'].values()
                          if entry['scope'] == scope and entry['token'] == token), None)
            if not entry:
                raise Failure('review', 'No fetched message receipt matches this token and account.')
            if resolve:
                if entry['outcome'] != 'needs-attention' or not entry['reviewedAt']:
                    raise Failure('review', 'Only an acknowledged needs-attention item can be resolved.')
                entry['resolvedAt'] = entry['resolvedAt'] or now()
            else:
                if entry['reviewedAt']:
                    if (entry['outcome'], entry['note'], entry['due']) != (outcome, note, due):
                        raise Failure('review', 'This receipt already has a different review outcome; resolve pending work explicitly.')
                else:
                    entry.update(reviewedAt=now(), outcome=outcome, note=note, due=due)
        return entry

    def status(self, scope):
        entries = [entry for entry in self.load()['entries'].values() if entry['scope'] == scope]
        return {'reviewedCount': sum(bool(entry['reviewedAt']) for entry in entries),
                'awaitingReview': [entry for entry in entries if not entry['reviewedAt']],
                'pending': [entry for entry in entries if entry['outcome'] == 'needs-attention' and not entry['resolvedAt']],
                'scopeCoverageComplete': False}


def execute(args, config):
    ledger = Ledger(config.state_dir)
    scope = config.scope()
    if args.command == 'review':
        if args.review_command == 'status':
            return ledger.status(scope)
        if args.review_command == 'ack':
            if args.note is not None and (not args.note.strip() or '\0' in args.note):
                raise Failure('usage', '--note must contain nonempty text without NUL characters.')
            if args.outcome == 'needs-attention' and not (args.note and args.note.strip()):
                raise Failure('usage', 'A needs-attention acknowledgment requires --note with the concrete action.')
            if args.due is not None:
                try:
                    calendar_date(args.due)
                except ValueError:
                    raise Failure('usage', '--due must be an ISO calendar date (YYYY-MM-DD).') from None
            return {'receipt': ledger.update(scope, args.token, args.outcome, args.note, args.due)}
        return {'receipt': ledger.update(scope, args.token, resolve=True)}
    backend = Backend(config)
    if args.command == 'status':
        return {'account': config.account, 'himalayaVersion': backend.version,
                'configuration': str(config.path),
                'himalayaConfig': str(config.himalaya_config), 'stateDirectory': str(config.state_dir),
                'documentation': str(documentation()),
                'networkChecked': False, **ledger.status(scope)}
    if args.command == 'doctor':
        report = backend.call(['account', 'check'])
        checks = table(report, 'backends')
        if len(checks) != 1 or checks[0].get('backend') != 'imap' or checks[0].get('ok') is not True:
            raise Failure('authentication', 'Himalaya could not validate the IMAP connection; its backend result failed even if the process exited zero. Do not repeatedly retry a rejected credential.')
        return {'account': config.account, 'himalayaVersion': backend.version, 'imap': {'ok': True}}
    if args.command == 'mailboxes':
        result = backend.call(['mailbox', 'list'])
        table(result, 'mailboxes')
        return result
    mailbox = config.mailbox(args.mailbox)
    epoch = backend.epoch(mailbox)
    if args.command in ('list', 'search'):
        command = ['envelope', 'list' if args.command == 'list' else 'search',
                   f'--mailbox={mailbox}', '--page', str(args.page), '--page-size', str(args.page_size)]
        if args.command == 'search':
            command.extend(['--', args.query])
        result = backend.call(command)
        rows = table(result, 'envelopes')
        if len(rows) > args.page_size:
            raise Failure('protocol', 'Himalaya exceeded the requested page size.')
        if backend.epoch(mailbox) != epoch:
            raise Failure('identity', 'Mailbox UIDVALIDITY changed during retrieval; retry the bounded query.')
        entries = ledger.load()['entries']
        for row in rows:
            try:
                uid = positive_uid(row.get('id', ''))
            except argparse.ArgumentTypeError:
                raise Failure('protocol', 'Himalaya returned an invalid IMAP UID.') from None
            receipt = entries.get(ledger.key(scope, mailbox, epoch, uid))
            row['review'] = {'reviewed': bool(receipt and receipt['reviewedAt']),
                             'receipt': receipt['token'] if receipt else None}
        return {'mailbox': mailbox, 'uidValidity': epoch, 'page': args.page, 'pageSize': args.page_size,
                'mayHaveMore': len(rows) == args.page_size, 'scopeCoverageComplete': False, **result}
    if args.command == 'read':
        result = backend.call(['message', 'read', f'--mailbox={mailbox}', '--raw', '--', args.uid])
        raw = result.get('message')
        if not isinstance(raw, str) or not raw.strip():
            raise Failure('protocol', 'Himalaya returned no complete raw message; no receipt was stored.')
        if backend.epoch(mailbox) != epoch:
            raise Failure('identity', 'Mailbox UIDVALIDITY changed during the read; no receipt was stored.')
        receipt = ledger.receipt(scope, config.account, mailbox, epoch, args.uid, raw)
        return {'mailbox': mailbox, 'uidValidity': epoch, 'uid': args.uid, 'message': raw,
                'review': {'receipt': receipt['token'], 'reviewed': bool(receipt['reviewedAt'])}}
    if args.command == 'attachments':
        result = backend.call(['attachment', 'list', f'--mailbox={mailbox}', '--', args.uid])
        table(result, 'attachments')
    else:
        target = Path(args.target).expanduser().absolute()
        try:
            target.mkdir(mode=0o700)
        except FileExistsError:
            raise Failure('destination', 'Attachment --target must be a new directory; existing files are not replaced.') from None
        result = backend.call(['attachment', 'download', f'--mailbox={mailbox}', '--dir', str(target),
                               '--', args.uid, *args.parts])
        rows = table(result, 'attachments')
        if {row.get('id') for row in rows} != set(args.parts) or len(rows) != len(args.parts):
            raise Failure('download', 'Not every requested attachment was returned; the new target may contain partial downloads.')
        for row in rows:
            path = Path(row.get('path') or '')
            if not path.is_absolute() or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(target.resolve()):
                raise Failure('download', 'An attachment result was outside the new target or not a regular file.')
            if path.stat().st_size != row.get('size'):
                raise Failure('download', 'An attachment size did not match; the target contains unverified downloads.')
    if backend.epoch(mailbox) != epoch:
        raise Failure('identity', 'Mailbox UIDVALIDITY changed during attachment retrieval; results are incomplete.')
    return {'mailbox': mailbox, 'uidValidity': epoch, 'uid': args.uid, **result}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure('usage', message)


def parser():
    root = Parser(description=__doc__)
    root.add_argument('--version', action='version', version=f'icloud-mail {VERSION}')
    root.add_argument('--config', type=Path, default=DEFAULT_CONFIG, help='private toolkit mail TOML; credentials remain in Himalaya/Keychain')
    root.add_argument('--json', action='store_true', help='emit one structured result')
    commands = root.add_subparsers(dest='command', required=True)
    for name in ('status', 'doctor', 'mailboxes'):
        commands.add_parser(name, help={'status': 'inspect local installation and review state; no network',
                                       'doctor': 'check the IMAP connection without modifying mail',
                                       'mailboxes': 'list mailboxes'}[name])
    for name in ('list', 'search', 'read', 'attachments', 'download'):
        sub = commands.add_parser(name)
        sub.add_argument('--mailbox', required=True, help='explicit mailbox name or configured alias')
        if name in ('list', 'search'):
            sub.add_argument('--page', type=int, default=1)
            sub.add_argument('--page-size', type=bounded_number, default=20)
            if name == 'search':
                sub.add_argument('--query', required=True, help='one Himalaya search DSL expression')
        else:
            sub.add_argument('uid', type=positive_uid)
            if name == 'download':
                sub.add_argument('--part', dest='parts', action='append', required=True, type=positive_uid)
                sub.add_argument('--target', required=True, help='new local directory for only the requested parts')
    reviews = commands.add_parser('review', help='local review acknowledgments and pending actions')
    actions = reviews.add_subparsers(dest='review_command', required=True)
    actions.add_parser('status')
    ack = actions.add_parser('ack')
    ack.add_argument('token')
    ack.add_argument('--outcome', choices=['clear', 'needs-attention'], required=True)
    ack.add_argument('--note', help='private local action summary')
    ack.add_argument('--due', help='private local deadline, YYYY-MM-DD')
    resolve = actions.add_parser('resolve')
    resolve.add_argument('token')
    return root


class Interrupted(BaseException):
    def __init__(self, number):
        self.number = number


def stop_for_signal(number, frame):
    raise Interrupted(number)


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    structured = '--json' in arguments
    try:
        args = parser().parse_args(arguments)
        if hasattr(args, 'page') and args.page < 1:
            raise Failure('usage', '--page must be at least 1')
        if hasattr(args, 'parts') and len(args.parts) != len(set(args.parts)):
            raise Failure('usage', 'Each attachment part must be selected only once.')
        config = Config.load(args.config)
        data = execute(args, config)
        result = {'schemaVersion': 1, 'version': VERSION, 'success': True, 'operation': args.command, 'data': data}
        print(json.dumps(result, ensure_ascii=True, indent=None if structured else 2))
        return 0
    except (Failure, OSError) as error:
        stage = error.stage if isinstance(error, Failure) else 'local-io'
        result = {'schemaVersion': 1, 'version': VERSION, 'success': False,
                  'error': {'stage': stage, 'message': str(error)}}
        print(json.dumps(result, ensure_ascii=True, indent=None if structured else 2), file=sys.stdout if structured else sys.stderr)
        return 2 if stage == 'usage' else 1
    except (KeyboardInterrupt, Interrupted) as error:
        number = error.number if isinstance(error, Interrupted) else signal.SIGINT
        result = {'schemaVersion': 1, 'version': VERSION, 'success': False,
                  'error': {'stage': 'interrupted', 'message': 'Mail operation interrupted; inspect local review state before retrying.'}}
        print(json.dumps(result), file=sys.stdout if structured else sys.stderr)
        return 128 + number


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, stop_for_signal)
    sys.exit(main())
