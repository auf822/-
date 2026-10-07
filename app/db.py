"""SQLite persistence. All business writes run in an IMMEDIATE transaction."""
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
 name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','counselor','secretary','advisor','student')),
 enabled INTEGER NOT NULL DEFAULT 1, major TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
 score REAL NOT NULL DEFAULT 0, year_id INTEGER REFERENCES years(id), grants TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS years (
 id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, stage TEXT NOT NULL DEFAULT 'preparation',
 wish_limit INTEGER NOT NULL DEFAULT 3 CHECK(wish_limit BETWEEN 1 AND 10),
 r1_start TEXT, r1_end TEXT, r2_start TEXT, r2_end TEXT,
 major_caps TEXT NOT NULL DEFAULT '{}', approved_at TEXT,
 current INTEGER NOT NULL DEFAULT 0 CHECK(current IN (0,1))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_current ON years(current) WHERE current=1;
CREATE TABLE IF NOT EXISTS profiles (
 advisor_id INTEGER PRIMARY KEY REFERENCES users(id), bio TEXT NOT NULL DEFAULT '',
 direction TEXT NOT NULL DEFAULT '', projects TEXT NOT NULL DEFAULT '', achievements TEXT NOT NULL DEFAULT '',
 notice TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS profile_requests (
 id INTEGER PRIMARY KEY, advisor_id INTEGER NOT NULL REFERENCES users(id),
 content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, review_note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS quotas (
 year_id INTEGER NOT NULL REFERENCES years(id), advisor_id INTEGER NOT NULL REFERENCES users(id),
 total INTEGER NOT NULL CHECK(total>=0), PRIMARY KEY(year_id,advisor_id)
);
CREATE TABLE IF NOT EXISTS applications (
 id INTEGER PRIMARY KEY, year_id INTEGER NOT NULL REFERENCES years(id),
 student_id INTEGER NOT NULL REFERENCES users(id), advisor_id INTEGER NOT NULL REFERENCES users(id),
 round INTEGER NOT NULL CHECK(round IN (1,2)), rank INTEGER NOT NULL CHECK(rank>0),
 status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, reviewed_at TEXT, note TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS pending_choice ON applications(year_id,student_id,advisor_id) WHERE status='pending';
CREATE TABLE IF NOT EXISTS matches (
 id INTEGER PRIMARY KEY, year_id INTEGER NOT NULL REFERENCES years(id), student_id INTEGER NOT NULL REFERENCES users(id),
 advisor_id INTEGER NOT NULL REFERENCES users(id), round INTEGER NOT NULL, source TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(year_id,student_id)
);
CREATE TABLE IF NOT EXISTS round2_eligible (
 year_id INTEGER NOT NULL REFERENCES years(id), student_id INTEGER NOT NULL REFERENCES users(id),
 PRIMARY KEY(year_id,student_id)
);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), title TEXT NOT NULL,
 body TEXT NOT NULL, created_at TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id), action TEXT NOT NULL,
 detail TEXT NOT NULL, created_at TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), csrf TEXT NOT NULL,
 expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, started INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS year_snapshots (
 year_id INTEGER PRIMARY KEY REFERENCES years(id), matches TEXT NOT NULL, sheets TEXT NOT NULL, stats TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS snapshot_no_update BEFORE UPDATE ON year_snapshots BEGIN SELECT RAISE(ABORT,'archive is read-only'); END;
CREATE TRIGGER IF NOT EXISTS snapshot_no_delete BEFORE DELETE ON year_snapshots BEGIN SELECT RAISE(ABORT,'archive is read-only'); END;
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def db_path():
    return Path(os.environ.get('DATABASE_PATH', Path(__file__).resolve().parent.parent / 'data/system.sqlite3'))


def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), timeout=20, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=20000')
    return c


def init_db():
    c = connect()
    try:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript(SCHEMA)
    finally:
        c.close()


@contextmanager
def transaction():
    c = connect()
    try:
        c.execute('BEGIN IMMEDIATE')
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def password_hash(password):
    salt = secrets.token_hex(16)
    result = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f'scrypt${salt}${result}'


def password_ok(password, stored):
    try:
        _, salt, expected = stored.split('$')
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def audit(c, actor, action, detail):
    stamp = now()
    payload = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    row = c.execute('SELECT hash FROM audit ORDER BY id DESC LIMIT 1').fetchone()
    prev = row['hash'] if row else '0' * 64
    digest = hashlib.sha256(f'{prev}|{actor}|{action}|{payload}|{stamp}'.encode()).hexdigest()
    c.execute('INSERT INTO audit(actor_id,action,detail,created_at,prev_hash,hash) VALUES(?,?,?,?,?,?)',
              (actor, action, payload, stamp, prev, digest))


def notify(c, users, title, body):
    for uid in set(users):
        c.execute('INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)',
                  (uid, title, body, now()))


DEFAULT_GRANTS = {
    'counselor': ['people', 'rules', 'matches', 'profiles', 'reports', 'reminders', 'quota_adjust'],
    'secretary': ['quota_initial', 'quota_reports'],
    'admin': [], 'advisor': [], 'student': []
}


def create_user(c, username, password, name, role, major='', title='', score=0, year_id=None, grants=None):
    uid = c.execute('INSERT INTO users(username,password_hash,name,role,major,title,score,year_id,grants) VALUES(?,?,?,?,?,?,?,?,?)',
                    (username, password_hash(password), name, role, major, title, score, year_id,
                     json.dumps(DEFAULT_GRANTS[role] if grants is None else grants))).lastrowid
    if role == 'advisor':
        c.execute('INSERT INTO profiles(advisor_id) VALUES(?)', (uid,))
    return uid
