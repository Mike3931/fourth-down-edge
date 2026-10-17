import {expect,test} from '@playwright/test';

const briefing={version:'war-room-foundation-v1',as_of_at:new Date().toISOString(),timezone:'America/Chicago',today_count:0,
  observed_market_games:0,verified_bet365_count:0,qualified_count:0,decision:'NO_BET',briefing:'No opportunity qualifies.',latest_run:null,changes:[],games:[]};
const payload={version:'prospective-scores-v1',status:'RESEARCH ONLY',decision:'NO BET',count:1,evaluated_forecasts:0,
  sample_policy:'Separate horizon cohorts.',limitations:['Not a validated betting model.'],performance:[],records:[{
    id:'controlled-contract-only',game_id:'2099_06_SEA_DEN',model_version:'pros-controlled',feature_snapshot_id:'pros-f-controlled',
    horizon:'PROS_72H',created_at:new Date().toISOString(),current:true,state:'SCHEDULED',evaluation:null,change:null,
    outputs:{approach:'ratings-contract',home_win_prob:0.55,expected_margin:2,expected_total:44,margin_p10:-15,margin_p90:19,
      total_p10:30,total_p90:58,validation_status:'VALIDATION FAILED',approval_status:'research_only',research_only:true,
      missing:['Official inactive coverage unknown'],training_games:80},
  }]};

test.beforeEach(async({page})=>{
  await page.route('**/research-api/v1/research/briefing',r=>r.fulfill({json:briefing}));
});

test('pregame research explains estimates, uncertainty, missing data and NO BET on mobile',async({page})=>{
  await page.route('**/research-api/v1/research/prospective',r=>r.fulfill({json:payload}));
  await page.setViewportSize({width:375,height:812});
  await page.goto('/');await page.getByRole('button',{name:/enter local demo session/i}).click();
  const panel=page.getByRole('region',{name:'Pregame forecast tracking'});
  await expect(panel.getByText('No settled prospective sample yet.',{exact:false})).toBeVisible();
  await expect(panel.getByText(/Home win 55.0%/)).toBeVisible();
  await expect(panel.getByText(/80% model range/)).toBeVisible();
  await panel.getByText('Missing inputs and forecast details').click();
  await expect(panel.getByText('Official inactive coverage unknown')).toBeVisible();
  await expect(page.getByRole('heading',{name:'NO BET',exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('failed refresh and promoted payload cannot leave cached forecasts visible',async({page})=>{
  let mode=0;
  await page.route('**/research-api/v1/research/prospective',r=>mode===1?r.fulfill({status:503}):r.fulfill({json:mode===2?{...payload,status:'APPROVED'}:payload}));
  await page.goto('/');await page.getByRole('button',{name:/enter local demo session/i}).click();
  const panel=page.getByRole('region',{name:'Pregame forecast tracking'});
  await expect(panel.getByText(/Home win 55.0%/)).toBeVisible();
  for(mode of [1,2]){
    await panel.getByRole('button',{name:'Refresh forecasts'}).click();
    await expect(panel.getByText('Forecast tracking is unavailable. Cached estimates are hidden.')).toBeVisible();
    await expect(panel.getByText(/Home win 55.0%/)).toHaveCount(0);
  }
});

test('evaluation states distinguish blocked, pending and evaluated records',async({page})=>{
  let state='FINAL - PENDING EVALUATION';
  await page.route('**/research-api/v1/research/prospective',r=>r.fulfill({json:{...payload,
    evaluated_forecasts:state==='EVALUATED'?1:0,
    records:[{...payload.records[0],current:false,state:'FINAL',evaluation_state:state,
      verification_warning:state==='BLOCKED'?'Missing pregame freeze attestation':null,
      evaluation:state==='EVALUATED'?{home_score:17,away_score:14,actual_margin:3,actual_total:31,
        brier:.2,margin_signed_error:-1,total_signed_error:14,winner_correct:true,
        evaluated_at:new Date().toISOString(),verification:'PUBLIC_PROVIDER_UNCONTESTED'}:null}]}}));
  await page.goto('/');await page.getByRole('button',{name:/enter local demo session/i}).click();
  const panel=page.getByRole('region',{name:'Pregame forecast tracking'});
  await expect(panel.getByText(state,{exact:true})).toBeVisible();
  state='BLOCKED';await panel.getByRole('button',{name:'Refresh forecasts'}).click();
  await expect(panel.getByText('Missing pregame freeze attestation',{exact:true})).toBeVisible();
  await expect(panel.getByText('Final score:',{exact:false})).toHaveCount(0);
  state='EVALUATED';await panel.getByRole('button',{name:'Refresh forecasts'}).click();
  await expect(panel.getByText('EVALUATED',{exact:true})).toBeVisible();
  await expect(panel.getByText(/Actual home margin 3.000/)).toBeVisible();
  await expect(panel.getByText(/Research only · NO BET/)).toBeVisible();
});
