import { expect, test } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';

test('populated operational workspace supports repeated navigation and refresh within two seconds', async ({page,request}) => {
  test.skip(process.env.FDE_E2E_OPERATIONAL !== '1', 'Requires the populated authorized operational workspace.');
  const forecasts=await (await request.get('/research-api/v1/research/prospective')).json();
  expect(forecasts.count).toBeGreaterThan(0);
  expect(forecasts.evaluated_forecasts).toBeGreaterThan(0);
  expect(forecasts.decision).toBe('NO BET');
  const slate=await (await request.get('/research-api/v1/forward/slate')).json();
  const game=slate.games.find((g:{kickoff_utc:string;canonical_game_id:string}) =>
    Date.parse(g.kickoff_utc)>Date.now() && forecasts.records.some((r:{game_id:string})=>r.game_id===g.canonical_game_id));
  expect(game).toBeTruthy();
  const times:{action:string;ms:number}[]=[];
  let start=Date.now();
  await page.goto('/');
  await page.getByRole('button',{name:/enter local demo session/i}).click();
  await expect(page.getByText(/Engine connected/)).toBeVisible();
  times.push({action:'cold War Room',ms:Date.now()-start});
  for(let repeat=1;repeat<=2;repeat++){
    start=Date.now();
    await page.getByRole('link',{name:'Slate',exact:true}).click();
    await expect(page.getByRole('table',{name:'Ingested fixtures'})).toBeVisible();
    times.push({action:`${repeat}: Slate`,ms:Date.now()-start});
    start=Date.now();
    await page.getByRole('link',{name:`${game.away_team_id} @ ${game.home_team_id}`,exact:true}).click();
    await expect(page.getByRole('region',{name:'Pregame forecast tracking'}).getByText(/Home win/).first()).toBeVisible();
    await expect(page.getByRole('region',{name:'NFL team and player research'})).toBeVisible();
    times.push({action:`${repeat}: Game Lab evidence and forecasts`,ms:Date.now()-start});
    const calls:string[]=[];
    const listener=(r:{url:()=>string})=>calls.push(r.url());
    page.on('request',listener);
    start=Date.now();
    const refreshed=page.waitForResponse(r=>r.url().endsWith('/research-api/v1/forward/live') && r.ok());
    await page.getByRole('button',{name:'Refresh analysis',exact:true}).click();
    await refreshed;
    await expect(page.getByRole('button',{name:'Refresh analysis',exact:true})).toBeEnabled();
    times.push({action:`${repeat}: Game Lab refresh`,ms:Date.now()-start});
    expect(calls.some(url=>url.endsWith('/research-api/v1/research/briefing'))).toBe(false);
    page.off('request',listener);
    start=Date.now();
    await page.getByRole('link',{name:'War Room',exact:true}).click();
    await expect(page.getByText(/Engine connected/)).toBeVisible();
    times.push({action:`${repeat}: return War Room`,ms:Date.now()-start});
  }
  start=Date.now();
  await page.getByRole('link',{name:'Candidates',exact:true}).click();
  await expect(page.getByText(/not a wager/i).first()).toBeVisible();
  times.push({action:'Candidates',ms:Date.now()-start});
  mkdirSync('qa/operational-recovery-20261017',{recursive:true});
  writeFileSync('qa/operational-recovery-20261017/browser-journey-times.json',JSON.stringify(times,null,2));
  for(const timing of times.filter(t=>!t.action.startsWith('cold'))) expect(timing.ms,timing.action).toBeLessThan(2000);
});

test('prospective tracking uses the real API and preserves research-only provenance',async({page,request})=>{
  const response=await request.get('/research-api/v1/research/prospective');
  expect(response.ok()).toBeTruthy();
  const data=await response.json();
  expect(data.decision).toBe('NO BET');
  expect(data.status).toBe('RESEARCH ONLY');
  expect(data.count).toBe(data.records.length);
  if(process.env.FDE_E2E_PROSPECTIVE!=='1') return;
  expect(data.count).toBeGreaterThan(0);
  expect(data.evaluated_forecasts).toBe(0);
  for(const record of data.records){
    expect(record.outputs.research_only).toBe(true);
    expect(record.outputs.approval_status).toBe('research_only');
    expect(Date.parse(record.created_at)).toBeLessThan(Date.parse(record.outputs.scheduled_kickoff));
    expect(record.model_version).toMatch(/^pros-/);
  }
  await page.goto('/');
  await page.getByRole('button',{name:/enter local demo session/i}).click();
  const panel=page.getByRole('region',{name:'Pregame forecast tracking'});
  await expect(panel.getByText(/No settled prospective sample yet/)).toBeVisible();
  await panel.getByRole('heading',{name:'Recorded before kickoff'}).scrollIntoViewIfNeeded();
  await page.screenshot({path:'qa/release1b4/war-room-desktop.png'});
  const slate=await(await request.get('/research-api/v1/forward/slate')).json();
  const game=slate.games.find((g:{canonical_game_id:string})=>data.records.some((r:{game_id:string})=>r.game_id===g.canonical_game_id));
  await page.goto(`/live/game/${game.canonical_game_id}`);
  await expect(panel.getByText(/Home win/).first()).toBeVisible();
  await panel.getByRole('heading',{name:'Recorded before kickoff'}).scrollIntoViewIfNeeded();
  await page.screenshot({path:'qa/release1b4/game-lab-desktop.png'});
  await page.setViewportSize({width:375,height:812});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await panel.getByRole('heading',{name:'Recorded before kickoff'}).scrollIntoViewIfNeeded();
  await page.screenshot({path:'qa/release1b4/game-lab-mobile.png'});
});

test('captured model comparison links real-game research and refuses recommendation status', async ({ page, request }) => {
  test.skip(process.env.FDE_E2E_MODEL_RESEARCH !== '1', 'Requires a separately executed genuine historical model study.');
  const response = await request.get('/research-api/v1/research/model-comparison');
  expect(response.ok()).toBeTruthy();
  const { report } = await response.json();
  expect(report.models).toHaveLength(3);
  expect(report.models.every((m: {n: number}) => m.n >= 1000)).toBeTruthy();
  expect(report.recommended_for_review).toHaveLength(0);
  expect(report.models.some((m: {status: string}) => m.status === 'VALIDATION FAILED')).toBeTruthy();
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await page.getByText('Model results and limitations (3 approaches)', {exact: true}).click();
  await expect(page.getByText(/Closing-market benchmark:/).first()).toBeVisible();
  await page.getByRole('region', {name: 'Statistical model research'}).screenshot({path:'qa/release1b3/model-war-room-desktop.png'});
  const slate = await (await request.get('/research-api/v1/forward/slate')).json();
  const game = slate.games.find((g: {kickoff_utc: string}) => Date.parse(g.kickoff_utc) > Date.now());
  await page.goto(`/live/game/${game.canonical_game_id}`);
  await page.getByText('Model results and limitations (3 approaches)', {exact: true}).click();
  await expect(page.getByText('Stored research estimate · team-ratings-v1')).toBeVisible();
  await expect(page.getByText(/Observed share of team opportunities:/).first()).toBeVisible();
  await page.getByRole('region', {name: 'Statistical model research'}).screenshot({path:'qa/release1b3/model-game-lab-desktop.png'});
  await page.setViewportSize({width:375,height:812});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('region', {name: 'Statistical model research'}).screenshot({path:'qa/release1b3/model-game-lab-mobile.png'});
});

/**
 * Separate, fail-closed acceptance gate. Unlike live-screens.spec.ts,
 * engine-down is NEVER acceptable in this test suite. The CI job must
 * start an actual FastAPI process against a migrated test database.
 *
 * An empty test slate is acceptable: this checks connectivity/provenance,
 * not an assertion that licensed NFL prices or real predictions exist.
 */
test('the real backend responds with a migrated database', async ({ request }) => {
  const health = await request.get('/research-api/health');
  expect(health.ok(), `backend /health returned ${health.status()}`).toBeTruthy();
  const body = await health.json();
  expect(body.database).toBe('ok');
  expect(body.auth).not.toMatch(/misconfigured/i);
  expect(body.research_banner).toMatch(/RESEARCH/i);

  const slate = await request.get('/research-api/v1/forward/slate');
  expect(slate.ok(), `forward slate returned ${slate.status()}`).toBeTruthy();
  const result = await slate.json();
  expect(result.data_mode).toBe('LIVE_RESEARCH');
  expect(result.count).toBe(result.games.length);
  expect(Array.isArray(result.games)).toBeTruthy();
});

test('the War Room uses the real engine rather than the demo fallback', async ({ page }) => {
  const response = page.waitForResponse((r) => r.url().endsWith('/research-api/v1/research/briefing'));
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  expect((await response).ok()).toBeTruthy();
  await expect(page).toHaveURL(/\/war-room$/);
  await expect(page.getByRole('heading', { name: 'The War Room' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'NO BET', exact: true })).toBeVisible();
  const briefing = await page.request.get('/research-api/v1/research/briefing');
  expect(briefing.ok()).toBeTruthy();
  expect((await briefing.json()).verified_bet365_count).toBe(0);
  await expect(page.getByText(/Engine connected · read/)).toBeVisible();
  await expect(page.getByText('The analytical engine is not reachable.')).toHaveCount(0);
  await expect(page.getByText('DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS')).toHaveCount(0);
});

test('War Room briefing matches the actual backend on desktop and mobile', async ({ page, request }) => {
  const response = await request.get('/research-api/v1/research/briefing');
  expect(response.ok()).toBeTruthy();
  const briefing = await response.json();
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await expect(page.getByRole('heading', { name: 'The War Room' })).toBeVisible();
  await expect(page.getByText(briefing.briefing, { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'NO BET', exact: true })).toBeVisible();
  if (process.env.FDE_E2E_REAL_CAPTURE === '1') {
    await page.screenshot({ path: 'qa/war-room/war-room-desktop.png', fullPage: true });
  }
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  if (process.env.FDE_E2E_REAL_CAPTURE === '1') {
    await page.screenshot({ path: 'qa/war-room/war-room-mobile.png', fullPage: true });
    expect(briefing.games.length).toBeGreaterThan(0);
    const game = briefing.games[0];
    const card = page.getByRole('article').filter({ hasText: `${game.away_team_id} at ${game.home_team_id}` });
    const link = card.getByRole('link', { name: 'Open Game Lab →' });
    await link.focus();
    await link.press('Enter');
    await expect(page.getByRole('heading', { name: `Game Lab · ${game.away_team_id} @ ${game.home_team_id}` })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Prepared research inputs' })).toBeVisible();
    await page.getByText('Missing evidence and qualification blockers', { exact: true }).click();
    await expect(page.getByText('Verified bet365 Tennessee prices', { exact: true })).toBeVisible();
  }
});

test('the live schedule comes from the backend, even when no games are seeded', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await page.goto('/slate');
  await expect(page.getByRole('heading', { name: 'Slate' })).toBeVisible();
  await expect(page.getByText('LIVE FROM ENGINE', { exact: true })).toBeVisible();
  await expect(page.getByText('The analytical engine is not reachable.')).toHaveCount(0);
});

test('data-health endpoint is reachable and its UI is not a demo', async ({ page, request }) => {
  const health = await request.get('/research-api/v1/forward/health');
  expect(health.ok(), `forward health returned ${health.status()}`).toBeTruthy();
  const data = await health.json();
  expect(data.data_mode).toBe('LIVE_RESEARCH');
  expect(Array.isArray(Object.values(data.by_scope).flat())).toBeTruthy();

  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await page.goto('/health');
  await expect(page.getByRole('heading', { name: 'Data Health' })).toBeVisible();
  await expect(page.getByText('LIVE FROM ENGINE', { exact: true })).toBeVisible();
  await expect(page.getByText('The analytical engine is not reachable.')).toHaveCount(0);
});

test('research candidate and captured-market API endpoints fail closed, not mock-backed', async ({ request }) => {
  for (const endpoint of ['/research-api/v1/forward/live', '/research-api/v1/forward/candidates', '/research-api/v1/forward/performance']) {
    const result = await request.get(endpoint);
    expect(result.ok(), `${endpoint} returned ${result.status()}`).toBeTruthy();
    const payload = await result.json();
    expect(payload.data_mode, `mode for ${endpoint}`).toBe('LIVE_RESEARCH');
    expect(payload.research_banner).toMatch(/RESEARCH/i);
  }
});


test('current public NFL captures navigate to Game Lab on desktop and mobile', async ({ page, request }) => {
  test.skip(process.env.FDE_E2E_REAL_CAPTURE !== '1', 'Separate opt-in public-provider journey; empty database gate does not prove ingestion.');
  const response = await request.get('/research-api/v1/forward/slate');
  expect(response.ok()).toBeTruthy();
  const slate = await response.json();
  const game = slate.games.find((g: { kickoff_utc: string }) => Date.parse(g.kickoff_utc) > Date.now());
  expect(game, 'current real capture must contain an upcoming fixture').toBeTruthy();
  const prepared = await request.get('/research-api/v1/research/briefing');
  expect(prepared.ok()).toBeTruthy();
  const evidence = (await prepared.json()).games.find((g: { canonical_game_id: string }) => g.canonical_game_id === game.canonical_game_id)?.intelligence;
  expect(evidence, 'public-source journey must include prepared player research').toBeTruthy();
  expect(evidence.sources.some((s: { dataset: string }) => s.dataset === 'player_stats')).toBeTruthy();
  expect(evidence.teams.some((t: { players: unknown[] }) => t.players.length > 0)).toBeTruthy();
  expect(evidence.teams.every((t: { availability_complete: boolean }) => t.availability_complete === false)).toBeTruthy();
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await page.goto('/slate');
  const showAll = page.getByRole('button', { name: 'show all weeks' });
  if (await showAll.isVisible()) await showAll.click();
  const matchup = page.getByRole('link', { name: `${game.away_team_id} @ ${game.home_team_id}`, exact: true });
  await matchup.focus();
  await matchup.press('Enter');
  await expect(page).toHaveURL(new RegExp(`/live/game/${game.canonical_game_id}$`));
  await expect(page.getByRole('heading', { name: `Game Lab · ${game.away_team_id} @ ${game.home_team_id}` })).toBeVisible();
  await expect(page.getByRole('table', { name: 'Latest observed market prices' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'NFL team and player research' })).toBeVisible();
  await page.getByText('Sources and timing', { exact: true }).click();
  await expect(page.getByText('player stats', { exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'espn / LIVE', exact: true }).first()).toBeVisible();
  await expect(page.getByText('DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS')).toHaveCount(0);
  await page.screenshot({ path: 'qa/handoff-2026-10-12/game-lab-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.reload();
  await expect(page.getByRole('heading', { name: `Game Lab · ${game.away_team_id} @ ${game.home_team_id}` })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expect(page.getByRole('list', { name: 'Observed market prices' })).toBeVisible();
  await page.screenshot({ path: 'qa/handoff-2026-10-12/game-lab-mobile.png', fullPage: true });
  await page.getByRole('link', { name: '← All games' }).click();
  await expect(page.getByRole('table', { name: 'Ingested fixtures' })).toBeVisible();
  await page.screenshot({ path: 'qa/handoff-2026-10-12/slate-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: 'qa/handoff-2026-10-12/slate-desktop.png', fullPage: true });
});
