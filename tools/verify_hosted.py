"""Verify retained hosted evidence against the exact current workbench package."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = 'https://bpmsoftwaresolutions-sidefx.hf.space'

def load(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))

def assess():
    findings, runs = [], []
    try:
        package = load('evidence/host/package.receipt.json')
        deployment = load('evidence/host/deployment.json')
        assert deployment['private'] and deployment['stage'] == 'RUNNING'
        assert deployment['commit'] == deployment['runningCommit']
        assert deployment['packageVersion']['contentDigest'] == package['contentDigest']
        examples = load('experiences/invoke/compiled/provider-resolution.dialog-plan.json')['input']['exampleSelector']['options']
        files = [subject + '-chromium.json' for subject in ['say-hello-world', 'greet-by-name', 'resolve-equity-market-price-evidence']]
        files += ['resolve-sidefx-eligible-providers-' + example + '.json' for example in examples]
        for file in files:
            proof = load('evidence/browser/' + file)
            assert proof['origin'] == ORIGIN and not proof['browserErrors'], file
            assert proof['packageVersion']['contentDigest'] == package['contentDigest'], 'stale browser evidence: ' + file
            snapshot = proof['snapshot']
            assert snapshot['selection']['publicationId'] == package['publicationId']
            assert snapshot['state'] == 'COMPLETED' and snapshot['result']['status'] == 'EXECUTED'
            assert snapshot['presentation'] and proof['dialogText']
            observed = [e for e in proof['browserEvents'] if e['event']['kind'] == 'execution.observation']
            before_terminal = [e for e in observed if e['receivedAt'] < snapshot['completedAt']]
            assert before_terminal, 'No reported observation reached the browser before completion: ' + file
            runs.append({'file':file,'runId':proof['runId'],'observations':len(observed),'observationsReceivedBeforeCompletion':len(before_terminal)})
        finance = load('evidence/browser/resolve-equity-market-price-evidence-chromium.json')['snapshot']['result']
        outcome = finance['outcome']
        # The capability performs the credential binding and bounded HTTP
        # exchange as declared effect ports inside its own scenario, so the
        # invocation envelope no longer carries transport evidence. Verify the
        # capability's own reported outcome against the request it executed.
        assert outcome['disposition'] == 'EQUITY_MARKET_PRICE_EVIDENCE_RESOLVED', outcome.get('disposition')
        submitted = finance['execution']['result']['input']['payload']
        assert outcome['payload']['symbol'] == submitted['symbol']
        assert isinstance(outcome['payload']['observedPrice'], (int, float)) and outcome['payload']['observedPrice'] > 0
        assert outcome['payload']['currency']
        assert outcome['providerTestimony']['providerId']
        interactions = load('evidence/browser/interaction-checks.json')
        assert {i['engine'] for i in interactions} == {'chromium','firefox','webkit'}
        assert all(i['passed'] and i['origin']==ORIGIN and i['packageVersion']['contentDigest']==package['contentDigest'] for i in interactions)
        recovery = load('evidence/browser/recovery.json')
        assert recovery['origin']==ORIGIN and recovery['packageVersion']['contentDigest']==package['contentDigest']
        assert recovery['explicitReconciliationDeduplicated'] and recovery['reloadSubmittedNothing'] and recovery['persistentAcrossServiceRestart']
    except (AssertionError, KeyError, FileNotFoundError, TypeError) as error:
        findings.append(str(error) or type(error).__name__)
    return {'receiptType':'hosted-workbench-verification.v1','passed':not findings,'origin':ORIGIN,'runs':runs,'findings':findings}

if __name__ == '__main__':
    result = assess()
    (ROOT/'evidence/host/verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)
