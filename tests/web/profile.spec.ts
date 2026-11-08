import { test, expect } from '@playwright/test';
const matches = ['processed','queued','available'].map((state,i)=>({matchId:100+i,heroId:1,startTime:1700000000,kills:8,deaths:2,assists:9,netWorth:24000,durationS:1800,won:i===0,state}));
async function mock(page, logged=false) {
 await page.route('**/api/me',r=>r.fulfill({json:{accountId:logged?42:null,steamId:'76561197960265770'}}));
 await page.route('**/api/players/*',r=>r.fulfill({json:{accountId:42,name:'Игрок <тест>',avatar:'',matches}}));
}
test('profile states, anonymous header, mobile',async({page})=>{
 await mock(page); await page.setViewportSize({width:390,height:844}); await page.goto('/p/42');
 await expect(page.getByRole('link',{name:'Войти через Steam'})).toHaveAttribute('href','/auth/login');
 await expect(page.getByRole('heading',{name:'Игрок <тест>'})).toBeVisible();
 await expect(page.getByRole('link',{name:'Обработан',exact:true})).toHaveAttribute('href','/m/100');
 await expect(page.getByText('В очереди / обрабатывается')).toBeVisible();
 await expect(page.getByRole('button',{name:'Войдите через Steam, чтобы обработать'})).toBeDisabled();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
 await page.screenshot({path:'test-results/profile-mobile.png',fullPage:true});
});
test('process job and logout',async({page})=>{
 await mock(page,true); await page.route('**/api/fetch',r=>r.fulfill({status:202,json:{job:'abc'}}));
 await page.route('**/api/jobs/abc',r=>r.fulfill({json:{state:'done',matchId:102}}));
 await page.route('**/auth/logout',r=>r.fulfill({json:{ok:true}})); await page.goto('/p/42');
 await expect(page.locator('[data-auth] a')).toHaveAttribute('href','/p/42');
 await page.getByRole('button',{name:'Обработать',exact:true}).click();
 await expect(page.getByRole('link',{name:'Обработан',exact:true})).toHaveCount(2);
 await page.getByRole('button',{name:'Выйти',exact:true}).click();
 await expect(page.getByRole('link',{name:'Войти через Steam'})).toBeVisible();
});
for(const code of [401,429,503,507]) test('fetch error '+code,async({page})=>{
 await mock(page,true); await page.route('**/api/fetch',r=>r.fulfill({status:code,json:{error:'Лимит или сервис недоступен'}}));
 await page.goto('/p/42'); await page.getByRole('button',{name:'Обработать',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('Лимит или сервис недоступен');
});
test('remote summary',async({page})=>{
 await mock(page,true); await page.route('**/api/matches/777',r=>r.fulfill({status:404,json:{error:'Нет матча'}}));
 await page.route('**/api/remote/777',r=>r.fulfill({json:{duration:1800,winningTeam:0,players:[{accountId:42,heroId:1,team:0,kills:5,deaths:2,assists:3,netWorth:15000}]}}));
 await page.goto('/m/777'); await expect(page.getByRole('link',{name:'42',exact:true})).toHaveAttribute('href','/p/42');
 await expect(page.getByRole('button',{name:'Обработать',exact:true})).toBeVisible();
});
test('search and upstream error',async({page})=>{
 await mock(page); await page.route('**/api/search?*',r=>r.fulfill({json:{redirect:'/p/42'}}));
 await page.goto('/'); await page.getByLabel('Матч или профиль Steam').fill('76561197960265770'); await page.getByRole('button',{name:'Найти',exact:true}).click();
 await expect(page).toHaveURL(/\/p\/42$/); await page.route('**/api/players/*',r=>r.fulfill({status:503,json:{error:'API временно недоступен'}}));
 await page.reload(); await expect(page.getByRole('alert')).toContainText('API временно недоступен');
});

test('duplicate processing and report nickname link',async({page})=>{
 await mock(page,true); await page.route('**/api/fetch',r=>r.fulfill({json:{matchId:102,duplicate:true}}));
 await page.goto('/p/42'); await page.getByRole('button',{name:'Обработать',exact:true}).click();
 await expect(page.getByRole('link',{name:'Обработан',exact:true})).toHaveCount(2);
 await page.goto('/m/133162474#players'); await expect(page.locator('#players a[href^="/p/"]').first()).toBeVisible();
});

test('queued match becomes processed',async({page})=>{
 await mock(page); let reads=0;
 await page.route('**/api/players/42',r=>r.fulfill({json:{accountId:42,name:'Игрок',avatar:'',matches:[{...matches[1],state:reads++?'processed':'queued'}]}}));
 await page.goto('/p/42'); await expect(page.getByText('В очереди / обрабатывается')).toBeVisible();
 await expect(page.getByRole('link',{name:'Обработан',exact:true})).toBeVisible({timeout:10000});
});
