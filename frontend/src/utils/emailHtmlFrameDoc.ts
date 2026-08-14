/** Build the srcdoc document for EmailHtmlFrame (kept out of the .vue SFC). */

export const EMAIL_FRAME_MSG = 'openmail-email-frame'

export function htmlHasRemoteImages(html: string): boolean {
  return /<img\b[^>]*\bsrc\s*=\s*['"]https?:\/\//i.test(html || '')
}

export function buildEmailFrameSrcdoc(
  bodyHtml: string,
  opts?: { allowRemoteImages?: boolean },
): string {
  const bridgeJs = `(function () {
  var MSG = ${JSON.stringify(EMAIL_FRAME_MSG)};
  function report() {
    var html = document.documentElement;
    var body = document.body;
    if (html) {
      html.style.setProperty('height', 'auto', 'important');
      html.style.setProperty('min-height', '0', 'important');
    }
    if (body) {
      body.style.setProperty('height', 'auto', 'important');
      body.style.setProperty('min-height', '0', 'important');
    }
    var bottom = 0;
    if (body) {
      var kids = body.children;
      for (var i = 0; i < kids.length; i++) {
        var el = kids[i];
        if (!el || el.tagName === 'SCRIPT') continue;
        var r = el.getBoundingClientRect();
        if (r.bottom > bottom) bottom = r.bottom;
      }
    }
    var h = Math.max(
      80,
      Math.ceil(bottom),
      body && body.scrollHeight ? body.scrollHeight : 0,
      html && html.scrollHeight ? html.scrollHeight : 0
    );
    parent.postMessage({ source: MSG, type: 'resize', height: h }, '*');
  }
  function onActivate(ev) {
    var t = ev.target;
    if (!t || !t.closest) return;
    var a = t.closest('a');
    if (!a) return;
    var href = a.getAttribute('href');
    if (!href) return;
    var low = href.trim().toLowerCase();
    if (low.indexOf('mailto:') === 0 || low.charAt(0) === '#' || low.indexOf('tel:') === 0) {
      return;
    }
    ev.preventDefault();
    ev.stopPropagation();
    parent.postMessage({ source: MSG, type: 'navigate', href: href }, '*');
  }
  document.addEventListener('click', onActivate, true);
  document.addEventListener('auxclick', onActivate, true);
  window.addEventListener('load', report);
  if (typeof ResizeObserver !== 'undefined' && document.body) {
    new ResizeObserver(report).observe(document.body);
  }
  setTimeout(report, 0);
  setTimeout(report, 200);
})();`

  const open = String.fromCharCode(60) + 'script>'
  const close = String.fromCharCode(60) + '/script>'
  const bridge = open + bridgeJs + close
  // sha256 of bridgeJs UTF-8; keep in sync (emailHtmlFrameDoc.spec.ts checks).
  const imgSrc = opts?.allowRemoteImages ? 'img-src data: https: cid:' : 'img-src data: cid:'
  const csp =
    `default-src 'none'; ${imgSrc}; style-src 'unsafe-inline'; ` +
    "script-src 'sha256-N0SiNp2Wh51KTGYQlwN+lqDogHkfabuYeXijjV3/JlY='; " +
    "object-src 'none'; base-uri 'none'; form-action 'none';"

  return [
    '<!DOCTYPE html><html><head><meta charset="utf-8" />',
    '<meta name="viewport" content="width=device-width, initial-scale=1" />',
    `<meta http-equiv="Content-Security-Policy" content="${csp}" />`,
    '<style>',
    'html, body { margin: 0; padding: 0; height: auto !important; min-height: 0 !important; max-height: none !important; }',
    'body { font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1a2333; word-wrap: break-word; overflow-wrap: anywhere; padding: 2px 0; }',
    'img { max-width: 100%; height: auto; }',
    'a { color: #1d4ed8; }',
    'pre, code { white-space: pre-wrap; word-break: break-word; }',
    'table { max-width: 100%; }',
    '</style></head><body>',
    bodyHtml,
    bridge,
    '</body></html>',
  ].join('')
}
