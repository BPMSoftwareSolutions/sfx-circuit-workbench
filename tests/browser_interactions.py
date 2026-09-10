"""Non-effectful UI checks on the actual generated surfaces in three engines."""
import argparse
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--origin', default='http://127.0.0.1:3013')
args = parser.parse_args()
results = []
headers = {'Authorization': 'Bearer ' + os.environ['HF_TOKEN']} if args.origin.startswith('https://') and os.environ.get('HF_TOKEN') else {}
with sync_playwright() as p:
    for engine in ['chromium', 'firefox', 'webkit']:
        browser = getattr(p, engine).launch(headless=True)
        page = browser.new_page(viewport={'width': 1220, 'height': 827}, reduced_motion='reduce', extra_http_headers=headers)
        errors, calls = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: calls.append(r.url) if r.method == 'POST' and r.url.endswith('/runs') else None)
        page.goto(args.origin + '/workbench/index.html?capability=greet-by-name')
        page.wait_for_function('window.SFX_WORKBENCH?.scene()?.invocation?.subject==="greet-by-name"')
        node = page.evaluate('SFX_WORKBENCH.scene().invocation.inputNode')
        page.locator('#' + node).focus()
        page.keyboard.press('Enter')
        dialog = page.get_by_role('dialog')
        dialog.wait_for()
        submit = dialog.get_by_role('button', name='Submit', exact=True)
        assert submit.is_disabled()
        assert dialog.evaluate('(e)=>getComputedStyle(e).opacity') == '1'
        field = dialog.locator('input')
        field.fill('x' * 101)
        assert submit.is_disabled()
        field.fill('Preserve me')
        assert submit.is_enabled()
        page.keyboard.press('Escape')
        dialog.wait_for(state='detached')
        assert page.evaluate('document.activeElement.id') == node
        page.keyboard.press('Enter')
        dialog = page.get_by_role('dialog')
        assert dialog.locator('input').input_value() == 'Preserve me'
        dialog.get_by_role('button', name='Cancel').click()
        assert not calls
        assert not errors
        results.append({'engine': engine, 'origin': args.origin, 'passed': True,
            'packageVersion':page.request.get(args.origin+'/workbench/package-version.json').json(),
            'checks': ['required input', 'contract length limit', 'reduced motion', 'keyboard opening', 'focus return', 'draft retention', 'no request on cancel']})
        browser.close()
out = Path(__file__).resolve().parent.parent / 'evidence/browser/interaction-checks.json'
out.write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
print(json.dumps(results))
