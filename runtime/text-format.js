/* Formatting against a declared text pack.
 *
 * The runtime holds no user-visible string. It asks for a named format or
 * message and supplies values; the pack decides the wording, the punctuation and
 * the glyphs. That is what makes wording swappable, keeps one phrasing per fact,
 * and confines every non-ASCII character in the interface to a single file that
 * an encoding check can guard completely.
 *
 * Two rules the resolver keeps:
 *
 *   * a missing template or a missing placeholder renders **visibly wrong**, not
 *     blank. A readout that silently renders empty is indistinguishable from a
 *     legitimate absence of data, which is the failure mode that hides a broken
 *     pack until somebody reads a screenshot.
 *   * a supplied value is inserted verbatim. This resolves text; it does not
 *     escape, translate or interpret it — escaping belongs to the projection,
 *     and a value's meaning belongs to the contract it came from.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) { module.exports = api; }
  else { root.SFX_TEXT_FORMAT = api; }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var PLACEHOLDER = /\{([a-zA-Z0-9_]+)\}/g;

  function create(pack, onFinding) {
    if (!pack || pack.textVersion !== "workbench-text.v1") {
      throw new Error("TEXT_PACK_UNSUPPORTED");
    }
    var unresolved = pack.unresolvedTemplate || "[{name} unresolved]";

    function report(code, name) {
      if (onFinding) { onFinding(code, name); }
      return unresolved.replace("{name}", name);
    }

    /* Glyphs are always available to a template, so a format never has to spell
     * a separator out and no two formats can disagree about it. */
    function fill(template, values, templateName) {
      var missing = [];
      var text = template.replace(PLACEHOLDER, function (match, name) {
        if (values && Object.prototype.hasOwnProperty.call(values, name)) {
          return String(values[name]);
        }
        if (Object.prototype.hasOwnProperty.call(pack.glyphs, name)) {
          return pack.glyphs[name];
        }
        missing.push(name);
        return match;
      });
      if (missing.length) {
        return report("TEXT_PLACEHOLDER_UNRESOLVED", templateName + ":" + missing.join(","));
      }
      return text;
    }

    function format(name, values) {
      var template = pack.formats[name];
      if (template === undefined) { return report("TEXT_FORMAT_UNDECLARED", name); }
      return fill(template, values, name);
    }

    function message(name, values) {
      var template = pack.messages[name];
      if (template === undefined) { return report("TEXT_MESSAGE_UNDECLARED", name); }
      return fill(template, values, name);
    }

    function glyph(name) {
      var value = pack.glyphs[name];
      if (value === undefined) { return report("TEXT_GLYPH_UNDECLARED", name); }
      return value;
    }

    /* A named lookup inside a declared map, so the runtime never carries the map
     * itself. `playLabel("running")` and `viewKindName("blueprint")` are the two
     * that exist today. */
    function lookup(group, name) {
      var table = pack[group];
      if (!table || table[name] === undefined) {
        return report("TEXT_LOOKUP_UNDECLARED", group + "." + name);
      }
      return table[name];
    }

    return {
      format: format,
      message: message,
      glyph: glyph,
      lookup: lookup,
      playLabel: function (state) { return lookup("playLabels", state); },
      viewKindName: function (kind) { return lookup("viewKindNames", kind); },
      pack: function () { return pack; }
    };
  }

  return { create: create };
});
