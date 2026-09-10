"""Lose an admission response, reload, reconcile once and optionally restart the service."""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--origin', default='http://127.0.0.1:3013')
parser.add_argument('--restart-service', action='store_true')
args = parser.parse_args()
headers = {'Authorization': 'Bearer ' + os.environ['HF_TOKEN']} if args.origin.startswith('https://') and os.environ.get('HF_TOKEN') else {}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(extra_http_headers=headers)
    page = context.new_page()
    accepted, requests = [], []

    def lose_response(route):
        if route.request.method != 'POST':
            return route.continue_()
        requests.append(route.request.post_data_json)
        response = route.fetch()
        accepted.append(response.json())
        if len(requests) == 1:
            route.abort('connectionreset')
        else:
            route.fulfill(response=response)

    page.route('**/workbench/runs', lose_response)
    url = args.origin + '/workbench/index.html?capability=greet-by-name'
    page.goto(url)
    page.wait_for_function('window.SFX_WORKBENCH?.scene()?.invocation?.subject==="greet-by-name"')
    node = page.evaluate('SFX_WORKBENCH.scene().invocation.inputNode')
    page.locator('#' + node).click()
    dialog = page.get_by_role('dialog')
    dialog.locator('input').fill('Recovery proof')
    dialog.get_by_role('button', name='Submit', exact=True).click()
    page.get_by_role('button', name='Reconcile this request').wait_for()
    page.reload()
    page.get_by_role('button', name='Reconcile this request').wait_for()
    assert len(requests) == 1, 'Reload must not submit an uncertain request automatically'
    page.get_by_role('button', name='Reconcile this request').click()
    page.wait_for_function('window.SFX_WORKBENCH_RUN', timeout=90000)
    assert len(requests) == 2 and requests[0] == requests[1]
    assert accepted[1]['deduplicated'] is True
    assert accepted[0]['runId'] == accepted[1]['runId']
    proof = page.evaluate('window.SFX_WORKBENCH_RUN')
    assert proof['snapshot']['state'] == 'COMPLETED'
    assert sum(e['kind'] == 'run.executing' for e in proof['snapshot']['events']) <= 1
    result = {'origin': args.origin, 'runId': proof['runId'], 'requestIdentityPreserved': True,
              'packageVersion':context.request.get(args.origin+'/workbench/package-version.json').json(),
              'explicitReconciliationDeduplicated': True, 'reloadSubmittedNothing': True,
              'completed': True, 'persistentAcrossServiceRestart': None}
    if args.restart_service:
        subprocess.run(['az.cmd', 'webapp', 'restart', '-g', 'sidefx_group', '-n', 'bpm-sidefx-lab-api'], check=True, capture_output=True)
        deadline = time.monotonic() + 120
        restored = None
        while time.monotonic() < deadline:
            try:
                response = context.request.get(args.origin + '/workbench/runs/' + proof['runId'], timeout=15000)
                if response.ok:
                    restored = response.json()
                    if restored.get('state') == 'COMPLETED' and restored.get('serviceInstanceId') != proof['snapshot'].get('serviceInstanceId'):
                        break
            except Exception:
                pass
            time.sleep(1)
        assert restored and restored['runId'] == proof['runId'] and restored['state'] == 'COMPLETED'
        assert restored.get('serviceInstanceId') and restored['serviceInstanceId'] != proof['snapshot'].get('serviceInstanceId')
        assert restored['result'] == proof['snapshot']['result']
        assert sum(e['kind'] == 'run.executing' for e in restored['events']) == 1
        result['persistentAcrossServiceRestart'] = True
        result['serviceInstances'] = [proof['snapshot'].get('serviceInstanceId'), restored['serviceInstanceId']]
    out = Path(__file__).resolve().parent.parent / 'evidence/browser/recovery.json'
    out.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    browser.close()
