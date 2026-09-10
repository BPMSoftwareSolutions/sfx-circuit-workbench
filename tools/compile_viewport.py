"""Lower the verified surface's declared stack/grid tracks to fluid HTML geometry.

Only physical layout is emitted. Existing component/state/action identities and
the sidefx-ui interaction plan remain intact; dialog surfaces are not affected.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def compile_viewport(output):
    profile = json.loads((ROOT/'profiles/viewport.host.json').read_text(encoding='utf-8'))
    source = json.loads((ROOT/profile['basisSurface']).read_text(encoding='utf-8'))
    prefix = 'body>.sfx-viewport'
    rules = [
        'html,body{width:100%;height:100%;margin:0;overflow:hidden;}',
        prefix+'{width:100%!important;height:100dvh!important;max-width:none;margin:0;overflow:hidden;}',
        prefix+' .ui-container{position:relative!important;left:auto!important;top:auto!important;width:auto!important;height:auto!important;min-width:0;min-height:0;}',
        prefix+' .ui-component{position:relative!important;left:auto!important;top:auto!important;width:100%!important;height:100%!important;min-height:0;}',
        prefix+' [data-region-id="root"]{width:100%!important;height:100%!important;}',
    ]

    def track(value):
        if not re.fullmatch(r'\d+(?:\.\d+)?(?:px|fr|%)', value):
            raise ValueError('VIEWPORT_TRACK_UNSUPPORTED:'+value)
        return 'minmax(0,'+value+')' if value.endswith('fr') else value

    def walk(node):
        ident = node['id']
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', ident): raise ValueError('UNSAFE_REGION_ID')
        selector = prefix+' [data-region-id="'+ident+'"]'
        layout = node.get('layout', {})
        padding = node.get('presentation',{}).get('padding',0)
        padding = padding if isinstance(padding,list) else [padding]
        declarations = ['display:grid','padding:'+' '.join(str(v)+'px' for v in padding)]
        children = []
        if not layout:
            if len(node.get('components',[]))>1: raise ValueError('VIEWPORT_REQUIRES_SINGLE_TENANT')
            declarations += ['grid-template-columns:minmax(0,1fr)','grid-template-rows:minmax(0,1fr)']
        elif layout['kind']=='stack':
            if layout.get('crossAlign','stretch')!='stretch': raise ValueError('VIEWPORT_ALIGNMENT_UNSUPPORTED')
            axis = 'columns' if layout['direction']=='row' else 'rows'
            declarations += ['grid-template-'+axis+':'+ ' '.join(track(t['size']) for t in layout['tracks']),
                'gap:'+str(layout.get('gap',0))+'px']
            children = [t['container'] for t in layout['tracks']]
        elif layout['kind']=='grid':
            declarations += ['grid-template-columns:'+' '.join(map(track,layout['columns'])),
                'grid-template-rows:'+' '.join(map(track,layout['rows'])),
                'column-gap:'+str(layout.get('columnGap',0))+'px','row-gap:'+str(layout.get('rowGap',0))+'px']
            for area in layout['areas']:
                child = area['container'];children.append(child)
                rules.append(prefix+' [data-region-id="'+child['id']+'"]{grid-column:%s/%s;grid-row:%s/%s;}' %
                    (area['column'][0]+1,area['column'][1]+1,area['row'][0]+1,area['row'][1]+1))
        else: raise ValueError('VIEWPORT_LAYOUT_UNSUPPORTED:'+layout['kind'])
        if ident=='root': declarations.append('grid-template-rows:'+' '.join(profile['rootRows']))
        if ident in profile['regionColumns']:
            declarations.append('grid-template-columns:'+' '.join(profile['regionColumns'][ident]))
        if node.get('presentation',{}).get('overflow')=='scroll': declarations.append('overflow:auto')
        rules.append(selector+'{'+';'.join(declarations)+'}')
        for child in children: walk(child)

    walk(source['surface']['rootContainer'])
    rules += [
        prefix+' [data-region-id="toolbar"]{overflow:auto;}',
        prefix+' [data-region-id="flow"]{overflow:auto;}',
        prefix+' [data-region-id="flow-status-row"]{min-height:20px;}',
        prefix+' .sfx-inspection-drawer{min-width:0;min-height:0;background:var(--panel);border-top:1px solid var(--rule);}',
        prefix+' .sfx-inspection-drawer>summary{height:32px;padding:6px 20px;cursor:pointer;font-size:12px;color:var(--ink);}',
        prefix+' .sfx-inspection-drawer>summary:focus-visible{outline:2px solid var(--focus);outline-offset:-3px;}',
        prefix+' .sfx-inspection-drawer[open]>.sfx-inspection-content{display:grid;grid-template-rows:'+' '.join(profile['inspection']['rows'])+';max-height:'+profile['inspection']['maxHeight']+';overflow:auto;}',
        prefix+' .sfx-inspection-drawer:not([open])>.sfx-inspection-content{display:none;}',
        prefix+' [data-region-id="inspection"]{--inspect:var(--panel);--inspect-ink:var(--ink);--inspect-muted:var(--muted);}',
        '@media(max-width:700px){'+prefix+' [data-region-id="header"]{grid-template-columns:minmax(0,1fr);grid-template-rows:58px 62px;padding:10px 16px;gap:0;}'+prefix+' [data-region-id="root"]{grid-template-rows:140px 64px 36px 46px minmax(0,1fr) 76px auto;}'+prefix+' [data-region-id="aside"]{display:none;}'+prefix+' [data-region-id="main"]{grid-template-columns:minmax(0,1fr);}}',
    ]
    (output/'viewport.css').write_text('\n'.join(rules)+'\n',encoding='utf-8')
    return {'inspection':profile['inspection']}
