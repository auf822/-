"""Explicit initialization; never runs automatically on server startup."""
import argparse
import getpass
import os
from datetime import datetime, timedelta, timezone
from .db import init_db, transaction, create_user, audit, notify
from .business import match_student


def demo(c):
    if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
        print('数据库已有账号，保留现有数据，不重复创建演示数据。')
        return
    t = datetime.now(timezone.utc)
    yid = c.execute("INSERT INTO years(name,current,stage,r1_start,r1_end,wish_limit,major_caps) VALUES(?,1,'round1',?,?,3,?)",
                    ('2026级研究生', (t - timedelta(days=2)).isoformat(), (t + timedelta(days=7)).isoformat(), '{"计算机科学与技术":20,"软件工程":20,"人工智能":20}')).lastrowid
    password = 'Demo2026!!'
    admin = create_user(c, 'admin', password, '系统管理员', 'admin')
    create_user(c, 'counselor', password, '李老师', 'counselor')
    create_user(c, 'secretary', password, '陈老师', 'secretary')
    specs = [
        ('张明远', '计算机科学与技术', '教授', '可信人工智能 / 大模型安全', '聚焦可信机器学习、大模型安全与隐私计算，探索人工智能在真实场景中的可靠应用。', 3),
        ('林清和', '软件工程', '教授', '软件工程 / 智能软件测试', '研究软件质量保障、程序分析和智能化开发工具，欢迎对工程实践与开源协作有热情的同学。', 3),
        ('王知行', '人工智能', '副教授', '计算机视觉 / 多模态学习', '开展多模态理解、视觉感知和医学影像研究，致力于让算法产生可验证的社会价值。', 2),
        ('周若宁', '计算机科学与技术', '教授', '分布式系统 / 云计算', '研究高性能分布式系统、云原生基础设施与数据密集型计算。', 3),
        ('刘星宇', '软件工程', '副教授', '人机交互 / 可视化分析', '关注以人为中心的交互技术、数据可视化与智能产品设计。', 2),
        ('沈亦舟', '人工智能', '教授', '自然语言处理 / 知识图谱', '研究语言智能与知识推理，关注检索增强生成和中文自然语言处理。', 3),
        ('赵文博', '计算机科学与技术', '教授', '网络与信息安全', '研究密码学与网络攻防。', 0),
    ]
    advisors = []
    for i, (name, major, title, direction, bio, total) in enumerate(specs, 1):
        uid = create_user(c, f'teacher{i:03}', password, name, 'advisor', major, title)
        advisors.append(uid)
        c.execute('UPDATE profiles SET bio=?,direction=?,projects=?,achievements=?,notice=? WHERE advisor_id=?',
                  (bio, direction, '国家自然科学基金项目；学院交叉学科研究计划。', '在相关领域国际期刊及会议发表多篇论文，指导学生参与科研实践。', '欢迎基础扎实、积极主动的同学申请。请在志愿提交前了解课题组研究方向。', uid))
        c.execute('INSERT INTO quotas VALUES(?,?,?)', (yid, uid, total))
    names = ['许知远', '江南', '陈思齐', '陆予安', '苏晓', '唐可欣', '程一诺', '何书瑶', '赵嘉宁', '方亦凡', '林沐', '吴清越']
    majors = ['计算机科学与技术', '软件工程', '人工智能']
    students = [create_user(c, f'student{i:03}', password, name, 'student', majors[(i-1) % 3], score=round(86 + i * 0.8, 1), year_id=yid) for i, name in enumerate(names, 1)]
    y = c.execute('SELECT * FROM years WHERE id=?', (yid,)).fetchone()
    for sid, aid in zip(students[:4], advisors[:4]):
        match_student(c, y, sid, aid, 1, 'advisor')
    for i in range(4, 9):
        aid = advisors[i % 6]
        c.execute("INSERT INTO applications(year_id,student_id,advisor_id,round,rank,created_at) VALUES(?,?,?,1,1,?)", (yid, students[i], aid, t.isoformat()))
        notify(c, [aid], '收到新的学生意向', f'{names[i]}提交了导师意向，请及时审核。')
    notify(c, students + advisors, '2026级首轮双选已开启', '请在截止时间前完成志愿提交与导师审核。如有疑问，请联系研究生辅导员。')
    audit(c, admin, 'demo.initialize', {'year': yid, 'advisors': len(advisors), 'students': len(students)})
    print('已创建演示数据。账号清单与演示密码见 README.md。')


def main():
    parser = argparse.ArgumentParser(description='初始化双选系统')
    parser.add_argument('--demo', action='store_true', help='仅空数据库：创建本地演示账号与数据')
    parser.add_argument('--admin', action='store_true', help='创建生产管理员（环境变量或交互输入）')
    args = parser.parse_args()
    if not args.demo and not args.admin:
        parser.error('请选择 --demo 或 --admin')
    if args.demo and args.admin:
        parser.error('不能同时创建演示和生产账号')
    init_db()
    with transaction() as c:
        if args.demo:
            demo(c)
        else:
            username = os.environ.get('ADMIN_USER') or input('管理员账号：')
            password = os.environ.get('ADMIN_PASSWORD') or getpass.getpass('管理员密码（至少10位）：')
            if len(password) < 10:
                raise SystemExit('密码必须至少10位')
            uid = create_user(c, username, password, os.environ.get('ADMIN_NAME', '系统管理员'), 'admin')
            audit(c, uid, 'admin.bootstrap', {'username': username})
            print('管理员已创建。登录后创建学年并授权管理岗。')


if __name__ == '__main__':
    main()
