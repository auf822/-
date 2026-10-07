import json
from datetime import datetime, timezone
from fastapi import HTTPException
from .db import now, notify, audit


def fail(detail, code=400):
    raise HTTPException(code, detail)


def require(user, roles, grant=None):
    if user['role'] not in roles:
        fail('没有此操作权限', 403)
    if grant and user['role'] in ('counselor', 'secretary') and grant not in json.loads(user['grants']):
        fail('此权限已被超级管理员收回', 403)


def current_year(c):
    y = c.execute('SELECT * FROM years WHERE current=1').fetchone()
    if not y:
        fail('请先由超级管理员创建学年', 409)
    return y


def instant(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        fail('时间格式不正确，请使用带时区的 ISO 时间')
    if dt.tzinfo is None:
        fail('时间必须包含时区')
    return dt.astimezone(timezone.utc)


def sync_deadlines(c):
    y = c.execute('SELECT * FROM years WHERE current=1').fetchone()
    if not y:
        return
    stage = y['stage']
    t = datetime.now(timezone.utc)
    if stage == 'round1' and y['r1_end'] and t >= instant(y['r1_end']):
        c.execute("UPDATE years SET stage='round1_closed' WHERE id=?", (y['id'],))
        c.execute('INSERT OR IGNORE INTO round2_eligible(year_id,student_id) SELECT ?,u.id FROM users u WHERE u.role=\'student\' AND u.year_id=? AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.student_id=u.id AND m.year_id=?)', (y['id'], y['id'], y['id']))
        stage = 'round1_closed'
    elif stage == 'round2' and y['r2_end'] and t >= instant(y['r2_end']):
        c.execute("UPDATE years SET stage='completed' WHERE id=?", (y['id'],))
        stage = 'completed'
    if stage != y['stage']:
        c.execute("UPDATE applications SET status='expired',reviewed_at=? WHERE year_id=? AND status='pending'", (now(), y['id']))
        people = [r['id'] for r in c.execute("SELECT id FROM users WHERE enabled=1 AND (role!='student' OR year_id=?)", (y['id'],))]
        notify(c, people, '本轮双选已截止', '志愿填报和导师审核入口已关闭，已配对结果保持锁定。')
        audit(c, None, 'round.deadline', {'year': y['id'], 'stage': stage})


def active_round(y):
    if y['stage'] not in ('round1', 'round2'):
        fail('当前填报与审核通道未开放', 409)
    r = 1 if y['stage'] == 'round1' else 2
    if not y[f'r{r}_start'] or not (instant(y[f'r{r}_start']) <= datetime.now(timezone.utc) < instant(y[f'r{r}_end'])):
        fail('当前不在本轮时间窗口内', 409)
    return r


def student_eligible(c, y, student, round_no):
    if student['role'] != 'student' or student['year_id'] != y['id'] or not student['enabled']:
        fail('学生不属于当前学年或账号已停用', 409)
    if c.execute('SELECT 1 FROM matches WHERE year_id=? AND student_id=?', (y['id'], student['id'])).fetchone():
        fail('已配对结果锁定，不能再次填报或录取', 409)
    if round_no == 2 and not c.execute('SELECT 1 FROM round2_eligible WHERE year_id=? AND student_id=?', (y['id'], student['id'])).fetchone():
        fail('仅首轮未匹配学生可以参加二轮补选', 403)


def remaining(c, year_id, advisor_id):
    quota = c.execute('SELECT total FROM quotas WHERE year_id=? AND advisor_id=?', (year_id, advisor_id)).fetchone()
    used = c.execute('SELECT COUNT(*) FROM matches WHERE year_id=? AND advisor_id=?', (year_id, advisor_id)).fetchone()[0]
    return (quota['total'] if quota else 0) - used


def check_capacity(c, year_id, advisor_id):
    u = c.execute('SELECT * FROM users WHERE id=?', (advisor_id,)).fetchone()
    if not u or u['role'] != 'advisor' or not u['enabled']:
        fail('导师不存在或已停用', 404)
    if remaining(c, year_id, advisor_id) <= 0:
        fail('该导师暂无法接收此志愿，请选择其他导师', 409)


def change_quota(c, y, uid, total):
    user = c.execute("SELECT * FROM users WHERE id=? AND role='advisor' AND enabled=1", (uid,)).fetchone()
    if not user:
        fail('导师不存在或已停用', 404)
    used = c.execute('SELECT COUNT(*) FROM matches WHERE year_id=? AND advisor_id=?', (y['id'], uid)).fetchone()[0]
    if total < used:
        fail('招生名额不能低于已配对人数', 409)
    c.execute('INSERT INTO quotas(year_id,advisor_id,total) VALUES(?,?,?) ON CONFLICT(year_id,advisor_id) DO UPDATE SET total=excluded.total', (y['id'], uid, total))


def validate_major_caps(c, y):
    caps = json.loads(y['major_caps'])
    for major, cap in caps.items():
        used = c.execute('SELECT COALESCE(SUM(q.total),0) FROM quotas q JOIN users u ON u.id=q.advisor_id WHERE q.year_id=? AND u.major=?', (y['id'], major)).fetchone()[0]
        if used > cap:
            fail(f'{major}导师招生名额合计 {used} 超过专业上限 {cap}', 409)


def quota_permission(user, y):
    if user['role'] == 'secretary':
        require(user, ['secretary'], 'quota_initial')
        if y['stage'] not in ('preparation', 'round1'):
            fail('首轮结束后的名额微调由辅导员负责', 403)
    else:
        require(user, ['counselor'], 'quota_adjust')
        if y['stage'] not in ('round1_closed', 'round2', 'completed'):
            fail('辅导员名额微调仅限首轮结束后', 409)


def invalidate_approval(c, y):
    c.execute('UPDATE years SET approved_at=NULL WHERE id=?', (y['id'],))


def match_student(c, y, sid, aid, round_no, source):
    check_capacity(c, y['id'], aid)
    if c.execute('SELECT 1 FROM matches WHERE year_id=? AND student_id=?', (y['id'], sid)).fetchone():
        fail('学生已经被其他导师录取', 409)
    c.execute('INSERT INTO matches(year_id,student_id,advisor_id,round,source,created_at) VALUES(?,?,?,?,?,?)', (y['id'], sid, aid, round_no, source, now()))
    c.execute("UPDATE applications SET status=CASE WHEN advisor_id=? THEN 'accepted' ELSE 'superseded' END,reviewed_at=? WHERE year_id=? AND student_id=? AND status='pending'", (aid, now(), y['id'], sid))
    invalidate_approval(c, y)
    notify(c, [sid, aid], '双选配对成功', '师生配对已生效并锁定。如需特殊调整，请联系研究生辅导员。')
