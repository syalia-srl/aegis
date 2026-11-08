// Lookups under a part's root, never over the page. Two mounts can hold the
// same ids (the plan keeps them as the browser suite's test hooks), so
// document.getElementById is not "yours": these take the element a part built
// and look only inside it.

export const q = (root, sel) => root.querySelector(sel);
export const qa = (root, sel) => [...root.querySelectorAll(sel)];
// CSS.escape: an id that is a keyword (a session id is not, but a caller's may
// be) would otherwise make the selector throw.
export const byId = (root, id) => root.querySelector(`#${CSS.escape(id)}`);
