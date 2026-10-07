// Run against an isolated --demo database: this exercises real writes.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.BASE_URL || 'http://127.0.0.1:8001';
const out = path.resolve(__dirname, '../test-results');
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', msg => { if (msg.type() === 'error' && /Content Security Policy|Refused to/.test(msg.text())) errors.push(msg.text()); });
  page.on('response', r => { if (r.status() >= 500) errors.push(`${r.status()} ${r.url()}`); });
  const nav = async name => {
    await page.locator('.nav').getByRole('button', { name, exact: true }).click();
    await page.waitForFunction(() => !document.getElementById('content').textContent.includes('正在加载'));
    assert(!(await page.locator('#content').textContent()).includes('没有此操作权限'));
  };
  const login = async username => {
    await page.goto(base);
    await page.locator('[name=username]').fill(username);
    await page.locator('[name=password]').fill('Demo2026!!');
    await page.getByRole('button', { name: '登录系统' }).click();
    await page.locator('.sidebar').waitFor();
    await page.waitForFunction(() => document.querySelector('#content h1'));
  };
  const logout = async () => {
    await page.getByRole('button', { name: '退出登录', exact: true }).click();
    await page.locator('[name=username]').waitFor();
  };

  await login('admin');
  await page.screenshot({ path: path.join(out, 'admin-dashboard.png'), fullPage: true });
  for (const name of ['账号与师生', '配对结果', '统计与归档', '双选配置', '操作日志', '消息中心']) await nav(name);
  await nav('操作日志');
  await page.getByRole('button', { name: '验证日志完整性' }).click();
  await page.getByRole('status').filter({ hasText: '日志完整性验证通过' }).waitFor();
  await logout();

  await login('student010');
  await nav('导师名录');
  assert.equal(await page.locator('.advisor-card').count(), 6);
  const directoryText = await page.locator('#content').textContent();
  assert(!directoryText.includes('剩余名额'));
  assert(!directoryText.includes('招生名额'));
  assert(!directoryText.includes('赵文博'));
  await page.screenshot({ path: path.join(out, 'student-directory.png'), fullPage: true });
  await page.getByRole('textbox', { name: '搜索导师' }).fill('张明远');
  assert.equal(await page.locator('.advisor-card').count(), 1);
  await page.getByRole('button', { name: '填报志愿', exact: true }).click();
  await page.getByRole('button', { name: '提交志愿', exact: true }).click();
  await page.locator('dialog').waitFor({ state: 'hidden' });
  await nav('我的志愿');
  assert((await page.locator('tbody').textContent()).includes('张明远'));
  await page.getByRole('button', { name: '修改', exact: true }).click();
  await page.locator('dialog select[name=rank]').selectOption('2');
  await page.getByRole('button', { name: '保存修改', exact: true }).click();
  await page.locator('dialog').waitFor({ state: 'hidden' });
  await page.waitForFunction(() => document.querySelector('tbody')?.textContent.includes('第 2 志愿'));
  await logout();

  await login('teacher001');
  await nav('学生意向');
  const studentRow = page.locator('tr', { hasText: '方亦凡' });
  await studentRow.getByRole('button', { name: '确认接收' }).click();
  await page.locator('dialog [name=note]').fill('研究方向吻合，确认接收');
  await page.getByRole('button', { name: '确认接收并锁定', exact: true }).click();
  await page.locator('dialog').waitFor({ state: 'hidden' });
  await page.waitForFunction(() => [...document.querySelectorAll('tbody tr')].some(r => r.textContent.includes('方亦凡') && r.textContent.includes('已录取')));
  await nav('录取结果');
  assert((await page.locator('tbody').textContent()).includes('方亦凡'));
  await nav('个人资料');
  await page.locator('[name=direction]').fill('可信人工智能 / 大模型安全 / 浏览器验收');
  await page.getByRole('button', { name: '提交辅导员审核' }).click();
  await page.getByRole('status').filter({ hasText: '资料已提交审核' }).waitFor();
  await logout();

  await login('student010');
  await nav('配对结果');
  assert((await page.locator('#content').textContent()).includes('张明远'));
  assert((await page.locator('#content').textContent()).includes('已确认并锁定'));
  await nav('我的志愿');
  assert.equal(await page.getByRole('button', { name: '新增志愿' }).count(), 0);
  await logout();

  await login('secretary');
  await nav('招生名额');
  const teacherRow = page.locator('tr', { hasText: '张明远' });
  await teacherRow.getByRole('button', { name: '调整名额' }).click();
  await page.locator('dialog [name=total]').fill('4');
  await page.getByRole('button', { name: '保存名额', exact: true }).click();
  await page.locator('dialog').waitFor({ state: 'hidden' });
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载填报名单' }).click();
  const download = await downloaded;
  assert(download.suggestedFilename().endsWith('.xlsx'));
  await download.saveAs(path.join(out, 'quotas.xlsx'));
  assert.equal(await page.locator('.nav').getByRole('button', { name: '配对结果' }).count(), 0);
  await logout();

  await login('counselor');
  await nav('资料审核');
  const pending = page.locator('tr', { hasText: '浏览器验收' });
  await pending.getByRole('button', { name: '查看并审核' }).click();
  await page.getByRole('button', { name: '提交审核结果', exact: true }).click();
  await page.locator('dialog').waitFor({ state: 'hidden' });
  await nav('双选配置');
  await page.getByRole('button', { name: '提前关闭首轮' }).click();
  await page.getByRole('button', { name: '确认关闭', exact: true }).click();
  await page.locator('dialog').waitFor({ state: 'hidden' });
  await page.waitForFunction(() => document.getElementById('content').textContent.includes('首轮已结束'));
  await nav('配对结果');
  await page.getByRole('button', { name: '生成调剂建议' }).click();
  await page.locator('dialog tbody tr').first().waitFor();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  assert.equal(await page.locator('dialog').isVisible(), false);
  for (const name of ['师生信息', '招生名额', '统计与归档', '消息中心', '工作台']) await nav(name);
  await page.screenshot({ path: path.join(out, 'counselor-dashboard.png'), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await page.locator('#content h1').waitFor();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.getByRole('button', { name: '展开导航' }).click();
  await nav('工作台');
  await page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 0);
  await page.screenshot({ path: path.join(out, 'mobile-dashboard.png'), fullPage: true });
  assert.deepEqual(errors, [], `Browser errors: ${errors.join('\n')}`);
  await browser.close();
  console.log('PASS: five roles, student choice edit, teacher acceptance, result lock, quota edit/export, profile approval, round close, plan preview, mobile layout; no JS/CSP/5xx errors.');
})().catch(e => { console.error(e); process.exit(1); });
