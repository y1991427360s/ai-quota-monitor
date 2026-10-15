// 离线浏览器回归：全部 API 使用脱敏样本，禁止请求真实账号或发送告警。
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(import.meta.dirname, '..');
const output = path.join(root, 'output/playwright');
await fs.mkdir(output, {recursive:true});
const stamp = '2026-10-15T12:00:00Z';
const source = 'offline-fixture (非真实账号)';
const window = (kind, percentage, seconds) => ({kind, percentage, remaining_percent:percentage, used_percent:percentage === null ? null : 100-percentage, window_seconds:seconds, reset_at:'2026-10-16T12:00:00Z', resetTime:'2026-10-16T12:00:00Z', source, fetched_at:stamp, status:percentage===null?'unknown':percentage===0?'exhausted':'available', stale:false});
const shared = {status:'exhausted',source,fetched_at:stamp,stale:false,windows:[window('five_hour',75.5,18000),window('weekly',0,604800)]};
const codex = [{email:'codex-demo@example.invalid',plan:'接口返回套餐',isActive:true,status:'available',source,fetched_at:stamp,rate_limits:shared,additional_rate_limits:[{id:'future-model-pool',name:'新模型专属池（接口样本）',model_id:'gpt-future-9',windows:[window('unknown',null,3600)]}],credits:{balance:12.5,unlimited:false,status:'available',source,fetched_at:stamp},chatgpt_web_quota:{status:'unavailable',reason:'当前授权未提供可靠网页端额度。'}}];
const model = (id,provider,pct,name=id) => ({id,rawModelId:id,displayName:name,provider,remainingPercent:pct,family:id.split('-')[0],version:'future',tier:'unknown-new-tier',status:pct===null?'unknown':pct===0?'exhausted':'available',source,fetched_at:stamp,resetTime:null});
const models = [model('gemini-4-future-argon-route','Google',65),model('gemini-3.8-flash-tiered','Google',null),model('gemini-3.6-flash-high','Google',0),model('gemini-3.5-flash-lite-agent','Google',90),model('claude-future-opus-thinking','Anthropic',38),model('gpt-future-sol','OpenAI',null),model('new-provider-unknown-model','Future Provider',null),model('gemini-3.1-pro-extra-low','Google',12)];
const google = [{email:'google-demo@example.invalid',tier:'接口返回套餐',status:'available',isActive:true,source,fetched_at:stamp,groups:[{groupName:'Gemini 共享额度组',windows:[window('five_hour',60,18000),window('weekly',20,604800)]},{groupName:'未知 Bucket 保留',windows:[{...window('unknown',null,90000),id:'future-bucket'}]}],models,downgrade_status:'unknown'}];
const poison = '<img src=x onerror="window.__xss=1">';
const errors = [{...codex[0],email:poison,status:'unavailable',error:poison,stale:true,rate_limits:{windows:[window('five_hour',null,18000),window('weekly',null,604800)]},additional_rate_limits:[],credits:{balance:null,status:'unavailable',source,fetched_at:stamp}}];
let useErrors = false;
const server = http.createServer(async(req,res)=> {
  if (req.url === '/favicon.ico') {res.writeHead(204).end();return;}
  res.setHeader('content-type','text/html; charset=utf-8');
  res.end(await fs.readFile(path.join(root,'frontend/index.html')));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try { browser = await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})}); }
catch (error) {
  if (process.env.PLAYWRIGHT_CHANNEL) throw error;
  browser = await chromium.launch({channel:'chrome',headless:true});
}
const report = {mode:'offline mocked API / no production account / no WeChat send',screenshots:[],checks:[]};
try {
  const page = await browser.newPage({viewport:{width:1440,height:1000},timezoneId:'Asia/Shanghai'});
  page.setDefaultTimeout(8000);
  const errorsSeen=[];
  page.on('pageerror',error=>errorsSeen.push(error.message));
  await page.route('**/*',async route=> {
    const url = new URL(route.request().url());
    if (url.pathname.includes('/api/')) {
      if (url.pathname.endsWith('/test-push')) throw new Error('测试禁止企业微信调用');
      let data={};
      if (url.pathname.endsWith('/chatgpt/quota')) data=useErrors?errors:codex;
      else if(url.pathname.endsWith('/quota')) data=google;
      else if(url.pathname.endsWith('/status')) data={accounts:google};
      else if(url.pathname.endsWith('/login-url')) data={login_url:'https://example.invalid/authorize',auth_url:'https://example.invalid/authorize',state:'offline-state'};
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
    } else if(url.hostname==='127.0.0.1') await route.continue();
    else await route.abort();
  });
  async function screenshot(name){await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});report.screenshots.push(name+'.png');console.log('截图完成：'+name);}
  async function noOverflow(){assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}
  await page.goto(`http://127.0.0.1:${server.address().port}/quota/`);
  await page.getByText('Codex 共享额度 · 单模型额度未提供').waitFor();
  assert.match(await page.locator('#contentArea').innerText(),/75.5%/);
  assert.match(await page.locator('#contentArea').innerText(),/0%/);
  assert.match(await page.locator('#contentArea').innerText(),/目前无法获取/);
  await screenshot('chatgpt');
  report.checks.push('Codex 5H/Weekly、专属未知池、Credits、网页额度不可用');
  await page.locator('#tabGoogleBtn').click();
  await page.getByText('Gemini 共享额度组').waitFor();
  await screenshot('google');
  assert.equal(await page.locator('.model-row').count(),6);
  await page.locator('[data-action="models"]').click();
  assert.equal(await page.locator('.model-row').count(),models.length);
  await page.locator('summary').filter({hasText:'展开原始 ID'}).first().click();
  await screenshot('models-expanded');
  await page.evaluate(()=>document.documentElement.classList.add('dark'));
  await screenshot('dark-models-expanded');
  await page.evaluate(()=>document.documentElement.classList.remove('dark'));
  await page.locator('#modelSearch').fill('claude-future');
  assert.equal(await page.locator('.model-row').count(),1);
  await page.locator('#modelSearch').fill('');
  await page.locator('#modelProvider').selectOption('OpenAI');
  assert.equal(await page.locator('.model-row').count(),1);
  await page.locator('#globalModelFilterBtn').click();
  assert.equal(await page.locator('.model-row').count(),0);
  await page.locator('#globalModelFilterBtn').click();
  await page.locator('#modelProvider').selectOption('');
  report.checks.push('实际未知模型展示、搜索、Provider 过滤、只看有额度、展开原始 ID');
  // A partial model-source failure must not mark a fresh summary source stale.
  await page.evaluate(data=>renderGoogleCards([{...data,stale:true,status:'stale',
    groups:data.groups.map(g=>({...g,stale:false,status:'available',source:'fresh-summary',fetched_at:'2026-10-15T12:00:00Z'})),
    models:data.models.map(m=>({...m,stale:true,status:'stale'}))}]),google[0]);
  assert.doesNotMatch(await page.locator('.group-section').first().innerText(),/stale/);
  assert.match(await page.locator('.model-row').first().innerText(),/stale/);
  report.checks.push('部分数据源失败时保留新鲜组额度，只有历史模型标记 stale');
  await page.evaluate(data=>renderGoogleCards(data),google);
  const tools=await page.evaluate(()=>({null:QuotaUI.percentValue(null),zero:QuotaUI.percentValue(0),full:QuotaUI.percentValue(100),bad:QuotaUI.percentValue('garbage'),iso:QuotaUI.dateValue('2026-10-15T20:00:00+08:00').toISOString(),unix:QuotaUI.dateValue(1792065600).toISOString()}));
  assert.equal(tools.null,null);assert.equal(tools.zero,0);assert.equal(tools.full,100);assert.equal(tools.bad,null);assert.equal(tools.iso,'2026-10-15T12:00:00.000Z');assert.equal(tools.unix,'2026-10-15T12:00:00.000Z');
  await page.evaluate(p=>renderGoogleCards([{email:p,status:'unavailable',error:p,models:[{id:p,displayName:p,provider:p,remainingPercent:null}],groups:[]}]),poison);
  assert.equal(await page.locator('#contentArea img').count(),0);
  assert.equal(await page.evaluate(()=>window.__xss),undefined);
  report.checks.push('模型/邮箱/错误 XSS 转义、null 非 100%、0/100 边界、带时区时间');
  useErrors=true;
  await page.locator('#tabCodexBtn').click();
  await page.locator('#contentArea .account-email').filter({hasText:poison}).waitFor();
  assert.equal(await page.locator('#contentArea img').count(),0);
  assert.doesNotMatch(await page.locator('#contentArea').innerText(),/100%/);
  await screenshot('abnormal-stale');
  report.checks.push('刷新失败历史状态、未知额度不伪造 100%');
  useErrors=false;
  await page.setViewportSize({width:390,height:844});
  await page.locator('#tabGoogleBtn').click();
  await page.getByText('google-demo@example.invalid',{exact:true}).waitFor();
  await noOverflow();
  await screenshot('mobile-google');
  await page.locator('[data-action="models"]').click();
  await page.locator('summary').filter({hasText:'展开原始 ID'}).first().click();
  await noOverflow();
  await screenshot('mobile-models-expanded');
  await page.locator('#tabCodexBtn').click();
  await page.getByText('codex-demo@example.invalid',{exact:true}).waitFor();
  await noOverflow();
  await screenshot('mobile-chatgpt');
  report.checks.push('390px 手机 Google / ChatGPT 页面无横向溢出');
  assert.deepEqual(errorsSeen,[]);
  report.checks.push('浏览器无未捕获 JavaScript 异常');
  report.passed=true;
} finally {
  await browser.close();
  server.closeAllConnections();
  await new Promise(resolve=>server.close(resolve));
  await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2));
}
console.log(JSON.stringify(report,null,2));
