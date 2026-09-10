"""Exercise the rendered workbench and its real service; retain browser evidence."""
import argparse
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser(); parser.add_argument('--origin',default='http://127.0.0.1:3013')
parser.add_argument('--subject',default='greet-by-name'); parser.add_argument('--name',default='Zoë <script>')
parser.add_argument('--example'); parser.add_argument('--symbol',default='AAPL'); parser.add_argument('--engine',default='chromium')
parser.add_argument('--inspect-only',action='store_true'); args=parser.parse_args()
root=Path(__file__).resolve().parent.parent
out=root/'evidence/browser'; out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    browser=getattr(p,args.engine).launch(headless=True)
    headers={}
    if args.origin.startswith('https://') and os.environ.get('HF_TOKEN'): headers['Authorization']='Bearer '+os.environ['HF_TOKEN']
    context=browser.new_context(viewport={'width':1220,'height':827},extra_http_headers=headers)
    page=context.new_page(); errors=[]; timeline=[]
    page.on('pageerror',lambda e: errors.append(str(e)))
    page.expose_function('retainEvent',lambda e: timeline.append(e))
    page.add_init_script("window.addEventListener('sfx-live-event', e=>window.retainEvent(e.detail))")
    page.goto(args.origin+'/workbench/index.html?capability='+args.subject)
    page.wait_for_function("window.SFX_WORKBENCH && window.SFX_WORKBENCH.scene()?.invocation?.subject === " + json.dumps(args.subject))
    scene=page.evaluate('window.SFX_WORKBENCH.scene()')
    page.locator('#'+scene['invocation']['inputNode']).click()
    dialog=page.get_by_role('dialog'); dialog.wait_for()
    page.wait_for_timeout(250)
    page.screenshot(path=str(out/(args.subject+'-input.png')),full_page=False)
    if args.inspect_only:
        print(json.dumps({'dialog':dialog.inner_text(),'errors':errors,'screenshot':str(out/(args.subject+'-input.png'))})); browser.close(); raise SystemExit()
    if dialog.locator('input').count():
        dialog.locator('input').first.fill(args.symbol if args.subject=='resolve-equity-market-price-evidence' else args.name)
    if dialog.locator('select').count():
        choice=dialog.locator('select').first
        available=choice.locator('option').evaluate_all('(nodes)=>nodes.map(n=>n.value).filter(Boolean)')
        choice.select_option(args.example or available[0])
    dialog.get_by_role('button',name='Submit',exact=True).click()
    page.wait_for_function("document.documentElement.hasAttribute('data-sfx-run-result')",timeout=150000)
    page.get_by_role('dialog').wait_for()
    page.wait_for_timeout(250)
    assert page.get_by_role('dialog').evaluate('(e)=>e.scrollTop') == 0, 'Outcome must open at its heading'
    proof=page.evaluate('window.SFX_WORKBENCH_RUN')
    proof['packageVersion']=context.request.get(args.origin+'/workbench/package-version.json').json()
    proof['browserEvents']=timeline; proof['browserErrors']=errors; proof['dialogText']=page.get_by_role('dialog').inner_text()
    proof['engine']=args.engine; proof['origin']=args.origin
    file=out/(args.subject+'-'+(args.example or args.engine)+'.json')
    file.write_text(json.dumps(proof,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    page.screenshot(path=str(out/(args.subject+'-outcome.png')),full_page=False)
    assert not errors,errors
    assert proof['snapshot'].get('presentation'), proof['snapshot'].get('presentationFinding') or proof['snapshot'].get('result',{}).get('code')
    assert proof['snapshot']['result']['status']=='EXECUTED'
    page.get_by_role('dialog').get_by_role('button',name='Close',exact=True).click()
    page.wait_for_timeout(250)
    page.locator('#'+scene['invocation']['outcomeNode']).click()
    page.get_by_role('dialog').wait_for()
    print(json.dumps({'subject':args.subject,'state':proof['snapshot']['state'],'events':len(timeline),
      'observations':sum(e['event']['kind']=='execution.observation' for e in timeline),
      'presented':bool(proof['snapshot'].get('presentation')),'evidence':str(file),'dialog':proof['dialogText'][:450]}))
    browser.close()
