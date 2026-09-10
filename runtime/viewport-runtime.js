/* Physical host geometry and disclosure; the compiled CSS owns all layout. */
(function () {
  'use strict';
  var config=JSON.parse(document.getElementById('sfx-workbench-config').textContent).viewport;
  if(!config)return;
  var surface=document.querySelector('body>.ui-surface');
  surface.classList.add('sfx-viewport');
  var root=surface.querySelector('[data-region-id="root"]');
  var drawer=document.createElement('details');drawer.className='sfx-inspection-drawer';
  var summary=document.createElement('summary');summary.textContent=config.inspection.label;
  var content=document.createElement('div');content.className='sfx-inspection-content';
  config.inspection.regions.forEach(function(id){content.append(root.querySelector('[data-region-id="'+id+'"]'));});
  drawer.append(summary,content);root.append(drawer);
  var circuit=surface.querySelector('[data-component-type="circuit"]');
  var pending=false, width, height;
  new ResizeObserver(function(){
    // Scrollbars change the content box during zoom, but not the host frame.
    // Only a real layout resize should replace the user's camera with Fit.
    var nextWidth=circuit.offsetWidth, nextHeight=circuit.offsetHeight;
    if(nextWidth===width && nextHeight===height)return;
    width=nextWidth;height=nextHeight;
    if(pending)return;pending=true;
    requestAnimationFrame(function(){
      pending=false;
      if(window.SFX_WORKBENCH){window.SFX_WORKBENCH.fit();window.dispatchEvent(new CustomEvent('sfx-viewport-resized'));}
    });
  }).observe(circuit,{box:'border-box'});
})();
