"""Package resolved source scenes and contract-generated dialogs for the shared host.

This consumes the verified database selection from read_invocation_authority.mjs.
The additional scenes are explicitly scenario interface views, not fabricated
mechanic graphs. They preserve the input/event/outcome relations returned by SQL.
"""
from __future__ import annotations
import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from compile_viewport import compile_viewport

ROOT = Path(__file__).resolve().parent.parent
UI = Path(os.environ.get('SIDEFX_UI_ROOT', 'C:/lab/sidefx-ui'))
OUT = ROOT / 'build/web/package'
load = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
def dump(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
def run(*args):
    result = subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
        env={**os.environ, 'PYTHONUTF8':'1', 'PYTHONIOENCODING':'utf-8'})
    if result.returncode: raise RuntimeError(result.stderr[-1500:] + result.stdout[-1000:])
def digest(data): return 'sha256:' + hashlib.sha256(data).hexdigest()

TRANSFORMATION_PORT = 'sda-authority-transformation-port.v1'

def declared_effect_ports(retained):
    """The capability's declared effect ports, read from its interface authority.

    A capability obtains external testimony through declared effect ports, not a
    provider-input binding document. The circuit is built from the same authority
    the planner materializes."""
    prefix = 'capabilities/' + retained['subject'] + '/interfaces.authority.json'
    records = retained['bundle']['authority']['recordsets'][1] + retained['bundle']['authority']['recordsets'][2]
    record = next((r for r in records if r.get('source_path') == prefix), None)
    if not record: return []
    document = json.loads(base64.b64decode(record['content_bytes']['base64']).decode('utf-8'))
    return [b for b in document.get('portBindings', []) if b.get('platformCapabilityId') != TRANSFORMATION_PORT]

def interface_scene(pilot, retained):
    subject = pilot['profile']['subject']; scenario = pilot['authority']['scenarioId']
    rows = retained['bundle']['authority']['recordsets'][0]
    selected = next(r for r in rows if r['scenario_id'] == scenario)
    source = {'label': 'Selected database scenario interface', 'sha256': selected['scenario_definition_digest'],
              'pointer': '/scenario/' + scenario}
    nodes, routes = [], []
    def node(key, kind, label, detail, identity=None, src=source):
        ident = identity or f'cell:scenario:{scenario}:{key}'
        item = {'id': 'i-' + hashlib.sha256(ident.encode()).hexdigest()[:20], 'identity': ident,
                'kind': kind, 'label': label, 'detail': detail, 'source': src, 'facts': {'scenarioId': scenario}}
        nodes.append(item); return item
    incoming = node('input', 'input', pilot['profile']['inputContract'], 'Select to enter capability input.')
    ports = declared_effect_ports(retained)
    provider = None
    if ports:
        exchange = next((p for p in ports if (p.get('configuration') or {}).get('endpointAuthorities')), None)
        endpoint = ((exchange or {}).get('configuration') or {}).get('endpointAuthorities', [{}])[0]
        origins = endpoint.get('urlPrefixes') or []
        provider = node('provider', 'provider-port', origins[0] if origins else 'Declared provider',
                        'Declared effect ports: ' + ', '.join(p.get('platformCapabilityId', '') for p in ports))
    event = node('event', 'event', selected['event_id'], selected['responsibility'])
    outcome = node('outcome', 'outcome', pilot['profile']['outcome']['contract'], 'Select to reopen this run’s outcome.')
    for index, (left, right) in enumerate(zip(nodes, nodes[1:])):
        routes.append({'id': f'r-{subject}-{index}', 'identity': f'{subject}:interface:{index}',
                       'source': left['id'], 'target': right['id'], 'kind': 'interface-binding',
                       'label': 'binding' if ports and index == 0 else 'scenario interface', 'provenance': source})
    width = len(nodes) * 280 + 60; height = 330
    boxes = {n['id']: [40 + i * 280, 95, 230, 125] for i, n in enumerate(nodes)}
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="group" aria-label="{html.escape(pilot["profile"]["label"])}">',
           '<defs><marker id="interface-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0 0 L8 4.5 L0 9" fill="none" stroke="#85bcc3"/></marker></defs>']
    for r in routes:
        x, y, w, h = boxes[r['source']]; tx, ty, tw, th = boxes[r['target']]
        svg.append(f'<g id="{r["id"]}" data-route="{r["id"]}" tabindex="0" role="button" aria-label="{html.escape(r["label"])}"><path class="route-path" d="M{x+w} {y+h/2} L{tx} {ty+th/2}" stroke="#85bcc3" stroke-width="2" fill="none" marker-end="url(#interface-arrow)"/></g>')
    for n in nodes:
        x,y,w,h = boxes[n['id']]; color = {'input':'#dcb678', 'event':'#4eddeb', 'outcome':'#72e1ad', 'provider-port':'#82a8f9'}[n['kind']]
        svg.append(f'<g id="{n["id"]}" data-entity="{n["id"]}" data-type="{n["kind"]}" tabindex="0" role="button" aria-label="{html.escape(n["kind"]+": "+n["label"])}"><title>{html.escape(n["identity"])}</title>')
        svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{40 if n["kind"]=="outcome" else 8}" fill="#0e2333" stroke="{color}" stroke-width="2"/>')
        svg.append(f'<text x="{x+16}" y="{y+28}" fill="{color}" font-family="monospace" font-size="11">{n["kind"].upper()}</text>')
        words = n['label'].replace('-', ' ').split(); lines = ['']
        for word in words:
            if len(lines[-1] + word) > 24: lines.append('')
            lines[-1] += (' ' if lines[-1] else '') + word
        for i, line in enumerate(lines[:3]): svg.append(f'<text x="{x+16}" y="{y+56+i*21}" fill="#dfebed" font-family="Arial" font-size="15">{html.escape(line)}</text>')
        svg.append('</g>')
    svg.append('</svg>'); svg = ''.join(svg)
    scene_id = 'invoke-' + subject
    scene = {'sceneVersion':'circuit-scene.v1', 'sceneId':scene_id, 'label':pilot['profile']['label'],
      'identities':{'capabilityId':subject,'viewId':scene_id,'viewKind':'invocation','scenarioId':scenario},
      'traceMode':'ILLUSTRATIVE','graph':{'nodes':nodes,'routes':routes},
      'geometry':{'width':width,'height':height,'boxes':boxes,'engine':'declared-interface-row'},
      'coverage':{'nodes':len(nodes),'routes':len(routes),'omittedSourceNodes':0,
        'scope':'Selected scenario input/event/outcome and declared effect ports. Internal mechanics are a separate view.'},
      'topology':{'roots':[incoming['id']],'leaves':[outcome['id']]}, 'findings':[], 'materials':[],
      'hitTargets':[{'targetId':n['id'],'entityId':n['id'],'keyboardActivable':True} for n in nodes+routes],
      'provenance':{'authority':pilot['authority'],'publicationId':retained['publicationId']},
      'invocation':{'subject':subject, 'inputNode':incoming['id'],'outcomeNode':outcome['id'],
        'providerNode':provider['id'] if provider else None, 'eventNode':event['id'],
        'stepNodes':{'admit-input':incoming['id'],
          'resolve-event-authority':event['id'],'execute-event-authority':event['id'],
          'admit-outcome':outcome['id'],'resolve-disposition':outcome['id']}}}
    return scene, svg

def main():
    run('tools/build_workbench.py')
    run('adapters/invocation/compile_capability_ux.py')
    run('adapters/invocation/build_dialog_surface.py')
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'build/source-inspection/package', OUT, dirs_exist_ok=True)
    document = (OUT/'index.html').read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="sfx-workbench-config">)(.*?)(</script>)', document, re.S)
    config = json.loads(match.group(2))
    # View-kind names are declared in the text pack, which the build already
    # placed in the config. Injecting one here would put presentation text in a
    # packaging step, where no profile could reach it.
    publication = load('C:/lab/repos/sfx-platform/generated/lab-publication.json')
    pilots = {p['profile']['subject']:p for p in publication['pilots']}
    web_manifest = {'publicationId':publication['publicationId'],'capabilities':[]}
    server_manifest = {'publicationId':publication['publicationId'],'capabilities':[]}
    stages = ROOT/'build/web/dialogs'; stages.mkdir(parents=True, exist_ok=True)
    for plan_path in sorted((ROOT/'experiences/invoke/compiled').glob('*.dialog-plan.json')):
        plan=load(plan_path); pilot=pilots[plan['subject']]; retained=load(ROOT/'build/invocation-authority'/(plan['subject']+'.json'))
        if retained['publicationId'] != publication['publicationId']: raise ValueError('SCENE_PUBLICATION_STALE')
        scene, svg = interface_scene(pilot, retained)
        stem=scene['sceneId']; dump(OUT/'scenes'/(stem+'.json'),scene)
        (OUT/'scenes'/(stem+'.svg')).write_text(svg,encoding='utf-8')
        config['scenes'].append({'viewId':stem,'viewKind':'invocation','label':pilot['profile']['label']+' · Run capability',
          'scene':'scenes/'+stem+'.json','artifact':'scenes/'+stem+'.svg','sha256':digest((OUT/'scenes'/(stem+'.json')).read_bytes())})
        resolved=ROOT/'build/invoke/stages'/('dialog-input-'+plan['uxId']+'.resolved.json')
        run(UI/'sidefx-project-ui-surface/project_ui_surface.py','--resolved',resolved,
            '--provider',ROOT/'providers/resolved/workbench-dark.provider.json','--out-dir',stages)
        projected=stages/('dialog-input-'+plan['uxId']+'.html')
        run(UI/'sidefx-html-interaction/realize_html_interaction.py','--resolved',resolved,'--html',projected,'--out-dir',stages)
        lowered=load(stages/('dialog-input-'+plan['uxId']+'.interactive.plan.json'))
        body=re.search(r'<body[^>]*>(.*?)</body>',projected.read_text(encoding='utf-8'),re.S).group(1)
        input_dialog={'html':body,'interaction':lowered, 'geometry':load(resolved)['surface']['dimensions'],
          'fields':plan['input']['fields'],'exampleSelector':plan['input']['exampleSelector'],
          'title':'Enter Capability Input','description':plan['input']['description'],
          'anchor':plan['input']['anchor'],'motion':plan['motion']}
        dump(OUT/'dialogs'/(plan['uxId']+'.json'),input_dialog)
        web_manifest['capabilities'].append({'subject':plan['subject'],'label':pilot['profile']['label'],
          'sceneId':stem,'dialog':'dialogs/'+plan['uxId']+'.json','inputAnchor':plan['input']['anchor'],
          'outcomeAnchor':plan['outcomes'][0]['anchor'], 'motion':plan['motion']})
        # Presentation families are resolved here; only physical operations and
        # bound values reach the browser. Contract/variant selection stays server-side.
        variants=[]
        ops={'text':'text','metric':'number-with-unit','disposition':'mapped-text','record':'key-value',
             'collection':'rows','findings':'rows','evidence':'key-value'}
        for variant in plan['outcomes']:
            elements=[{**e,'op':ops[e['family']]} for e in variant['elements']]
            for e in elements: del e['family']
            outcome_stem='dialog-outcome-'+plan['uxId']+'-'+variant['outcomeId']
            outcome_resolved=ROOT/'build/invoke/stages'/(outcome_stem+'.resolved.json')
            run(UI/'sidefx-project-ui-surface/project_ui_surface.py','--resolved',outcome_resolved,
                '--provider',ROOT/'providers/resolved/workbench-dark.provider.json','--out-dir',stages)
            outcome_projected=stages/(outcome_stem+'.html')
            run(UI/'sidefx-html-interaction/realize_html_interaction.py','--resolved',outcome_resolved,
                '--html',outcome_projected,'--out-dir',stages)
            outcome_body=re.search(r'<body[^>]*>(.*?)</body>',outcome_projected.read_text(encoding='utf-8'),re.S).group(1)
            layout_file='dialogs/'+outcome_stem+'.json'
            dump(OUT/layout_file,{'html':outcome_body,'geometry':load(outcome_resolved)['surface']['dimensions'],
                'interaction':load(stages/(outcome_stem+'.interactive.plan.json'))})
            variants.append({'outcomeId':variant['outcomeId'],'title':variant['dialogTitle'],'match':variant['match'],
              'anchor':variant['anchor'],'elements':elements,'layout':layout_file})
        server_manifest['capabilities'].append({'subject':plan['subject'],
          'outcomeContract':pilot['profile']['outcome']['contract'],'variants':variants})
    dump(OUT/'invocation.json',web_manifest)
    dump(ROOT/'build/web/workbench-publication.json',server_manifest)
    for file in ['invocation-runtime.js','outcome-components.js']:
        shutil.copyfile(ROOT/'runtime'/file,OUT/file)
    shutil.copyfile(UI/'sidefx-html-interaction/runtime/sidefx-ui-runtime.js',OUT/'interaction-runtime.js')
    config['invocationManifest']='invocation.json'
    config['viewport']=compile_viewport(OUT)
    config['embed']['reportHeight']=False
    shutil.copyfile(ROOT/'runtime/viewport-runtime.js',OUT/'viewport-runtime.js')
    document=document[:match.start(2)]+json.dumps(config).replace('<','\\u003c')+document[match.end(2):]
    document=document.replace('</head>','<link rel="stylesheet" href="viewport.css" /></head>')
    document=document.replace('<script src="workbench-runtime.js">','<script src="viewport-runtime.js"></script><script src="workbench-runtime.js">')
    document=document.replace('</body>', '<script src="outcome-components.js"></script><script src="invocation-runtime.js"></script></body>')
    (OUT/'index.html').write_text(document,encoding='utf-8')
    # Default entry stays the faithful source workbench. Runnable views are
    # selected in the same selector; the finance deep link is also retained.
    files={p.relative_to(OUT).as_posix():digest(p.read_bytes()) for p in OUT.rglob('*') if p.is_file() and p.name!='package-version.json'}
    content_digest=digest(json.dumps(files,sort_keys=True).encode())
    dump(OUT/'package-version.json',{'contentDigest':content_digest,'publicationId':publication['publicationId']})
    dump(ROOT/'evidence/host/package.receipt.json',{'package':str(OUT),'publicationId':publication['publicationId'],
      'contentDigest':content_digest,'scenes':len(config['scenes']),'dialogs':len(web_manifest['capabilities']),'files':files})
    print(json.dumps({'package':str(OUT),'scenes':len(config['scenes']),'dialogs':len(web_manifest['capabilities'])}))

if __name__=='__main__': main()
