"""Check the visible viewport, disclosure and dialog behavior without invoking."""
import argparse
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--origin', default='http://127.0.0.1:3012')
parser.add_argument('--label', default='local')
args = parser.parse_args()
out = Path(__file__).resolve().parent.parent / 'evidence/browser'
headers = {'Authorization': 'Bearer ' + os.environ['HF_TOKEN']} if args.origin == 'https://bpmsoftwaresolutions-sidefx.hf.space' and os.environ.get('HF_TOKEN') else {}
results = []

with sync_playwright() as p:
    for engine in ['chromium', 'firefox', 'webkit']:
        # Chromium headless normally hides scrollbars, masking camera resets
        # caused by ResizeObserver content-box notifications on Windows.
        launch = {'ignore_default_args':['--hide-scrollbars']} if engine == 'chromium' else {}
        browser = getattr(p, engine).launch(headless=True, **launch)
        page = browser.new_page(viewport={'width': 1909, 'height': 895}, extra_http_headers=headers)
        errors, invocations = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: invocations.append(r.url) if r.method == 'POST' and r.url.endswith('/runs') else None)
        page.goto(args.origin + '/workbench/index.html')
        page.wait_for_function('window.SFX_WORKBENCH?.scene()')

        def camera():
            # Let scrollbar layout and ResizeObserver callbacks settle.
            page.wait_for_timeout(150)
            return page.evaluate('SFX_WORKBENCH.camera()')

        initial = camera()
        page.get_by_role('button', name='+', exact=True).click()
        assert camera() > initial, 'Zoom in must survive scrollbar appearance'
        page.get_by_role('button', name='Read at 100%', exact=True).click()
        assert camera() == 1, 'Read at 100% must not snap back to Fit'
        assert page.evaluate('SFX_WORKBENCH.mount().scrollWidth > SFX_WORKBENCH.mount().clientWidth')
        page.locator('[data-component-type="circuit"]').hover()
        page.mouse.wheel(500, 300)
        page.wait_for_timeout(200)
        assert page.evaluate('SFX_WORKBENCH.mount().scrollLeft > 0 && SFX_WORKBENCH.mount().scrollTop > 0')
        for _ in range(3):
            page.get_by_role('button', name='+', exact=True).click()
        assert camera() == 2, 'Zoom must reach its declared 200% limit'
        page.get_by_role('button', name='−', exact=True).click()
        assert 1 < camera() < 2
        page.get_by_role('button', name='Fit diagram', exact=True).click()
        assert 0 < camera() < 1

        def bounds():
            page.wait_for_function('''() => {
                const e=document.querySelector('.sfx-viewport');
                return e && e.offsetWidth===innerWidth && e.offsetHeight===innerHeight;
            }''')
            return page.evaluate('''() => {
                const rect=s=>document.querySelector(s).getBoundingClientRect().toJSON();
                return {viewport:[innerWidth,innerHeight], document:[document.documentElement.scrollWidth,document.documentElement.scrollHeight],
                    surface:rect('.sfx-viewport'), circuit:rect('[data-component-type="circuit"]'),
                    details:rect('.sfx-inspection-drawer')};
            }''')

        sizes = []
        for width, height in [(1909,895),(1280,720),(2560,1440),(900,700),(390,844)]:
            page.set_viewport_size({'width':width,'height':height})
            b = bounds()
            assert b['surface']['x'] == b['surface']['y'] == 0, b
            assert b['document'] == b['viewport'] == [width,height], b
            assert b['circuit']['height'] > height * .35, b
            assert b['circuit']['width'] > width * .7, b
            assert abs(b['details']['bottom']-height) <= 1, b
            sizes.append(b)

        page.set_viewport_size({'width':1909,'height':895})
        closed = bounds()
        page.locator('.sfx-inspection-drawer>summary').click()
        page.wait_for_function('document.querySelector(".sfx-inspection-drawer").open')
        opened = bounds()
        assert opened['document'] == opened['viewport'], opened
        assert 0 < opened['circuit']['height'] < closed['circuit']['height'], opened
        assert page.locator('[data-region-id="inspection"]').is_visible()
        page.screenshot(path=str(out / (args.label+'-viewport-details-'+engine+'.png')))
        page.locator('.sfx-inspection-drawer>summary').click()
        page.screenshot(path=str(out / (args.label+'-viewport-'+engine+'.png')))

        page.goto(args.origin + '/workbench/index.html?capability=greet-by-name')
        page.wait_for_function('window.SFX_WORKBENCH?.scene()?.invocation?.subject==="greet-by-name"')
        node = page.evaluate('SFX_WORKBENCH.scene().invocation.inputNode')
        page.locator('#'+node).click()
        dialog = page.get_by_role('dialog')
        dialog.wait_for()
        dialog.locator('input').fill('Viewport check')
        page.set_viewport_size({'width':1280,'height':720})
        page.wait_for_function('''() => {
            const r=document.querySelector('[role="dialog"]').getBoundingClientRect();
            return r.x>=0 && r.y>=0 && r.right<=innerWidth && r.bottom<=innerHeight;
        }''')
        assert dialog.locator('input').input_value() == 'Viewport check'
        dialog.get_by_role('button', name='Cancel', exact=True).click()
        assert not invocations, invocations
        assert not errors, errors
        results.append({'engine':engine,'origin':args.origin,'passed':True,'sizes':sizes,
            'checks':['zoom with visible scrollbars','read at 100%','scroll enlarged diagram','zoom to 200% and back','fit diagram','edge-to-edge viewport','no document overflow','circuit fills remaining height','details disclosure','dialog after resize','draft retained','no invocation on cancel'],
            'packageVersion':page.request.get(args.origin+'/workbench/package-version.json').json()})
        browser.close()

(out / (args.label+'-viewport-checks.json')).write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'origin':args.origin,'engines':[r['engine'] for r in results],'passed':True}))
