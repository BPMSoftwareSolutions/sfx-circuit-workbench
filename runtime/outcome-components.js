/* Physical components consume already-bound values and rendering operations.
 * This layer selects no contracts, variants, units or business dispositions. */
(function (global) {
  'use strict';
  function text(value) {
    if (value === null) return 'None';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }
  function show(bound) { return !bound || !bound.present ? 'Not reported' : text(bound.value); }
  function element(tag, content, className) {
    var node = document.createElement(tag); if (content !== undefined) node.textContent = content;
    if (className) node.className = className; return node;
  }
  function rows(target, items) {
    var list = element('dl', undefined, 'sfx-result-fields');
    items.forEach(function (item) { list.append(element('dt', item.label), element('dd', show(item))); });
    target.append(list);
  }
  var operations = {
    'text': function (target, e) { target.append(element('p', show(e.values.value))); },
    'mapped-text': function (target, e) {
      var raw = show(e.values.value); target.append(element('p', e.dispositionLabels && e.dispositionLabels[raw] || raw, 'sfx-result-status'));
    },
    'number-with-unit': function (target, e) {
      var amount = e.values.amount, display = show(amount);
      if (amount && amount.present && typeof amount.value === 'number' && Number.isInteger(e.precision)) display = amount.value.toFixed(e.precision);
      var unit = e.values.currency || e.values.unit;
      target.append(element('p', display + (unit && unit.present ? ' ' + unit.value : ''), 'sfx-result-metric'));
      if (e.values.timestamp && e.values.timestamp.present) target.append(element('p', 'Market time · ' + new Date(Number(e.values.timestamp.value) * 1000).toISOString(), 'sfx-result-meta'));
    },
    'key-value': function (target, e) {
      rows(target, e.fields || Object.keys(e.values).map(function (key) { return Object.assign({ label: key }, e.values[key]); }));
    },
    'rows': function (target, e) {
      if (!e.rows || !e.rows.present) { target.append(element('p', 'Not reported')); return; }
      if (!Array.isArray(e.rows.value)) throw new Error('ROWS_SHAPE_INVALID');
      if (!e.rows.value.length) { target.append(element('p', e.emptyText || 'None')); return; }
      e.rows.value.forEach(function (row) {
        var card = element('div', undefined, 'sfx-result-row');
        if (row !== null && typeof row === 'object') {
          var keys = e.rowFields && e.rowFields.length ? e.rowFields.map(function (k) { return k.replace(/^\//, ''); }) : Object.keys(row);
          rows(card, keys.map(function (key) { return { label: key, present: Object.prototype.hasOwnProperty.call(row, key), value: row[key] }; }));
        } else card.textContent = text(row);
        target.append(card);
      });
    }
  };
  global.SFX_OUTCOME_COMPONENTS = { render: function (root, presentation) {
    presentation.elements.forEach(function (e) {
      var region = root.querySelector('[data-component-id="' + e.componentId + '"]');
      if (!region || !operations[e.op]) throw new Error('OUTCOME_COMPONENT_UNSUPPORTED');
      var content = element('section', undefined, 'sfx-result-component');
      content.append(element('h3', e.label || 'Result'));
      operations[e.op](content, e); region.replaceChildren(content);
    });
  } };
})(globalThis);
