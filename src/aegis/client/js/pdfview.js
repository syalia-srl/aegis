// Mozilla's PDF viewer (pdf.js), served from /pdfjs once `aegis pdfjs install`
// put it on the server (pdfjs.py). The welcome says whether it is there.
// Without it a PDF opens in the browser's own viewer, with no annotations.

let installed = false;

export function setPdfjs(on) {
  installed = on === true;
}

export function pdfjsReady() {
  return installed;
}

// The file URL has to be absolute and on this origin: the viewer refuses a
// file from anywhere else.
export function pdfViewerSrc(fileUrl) {
  if (!installed) return fileUrl;
  const file = new URL(fileUrl, location.href).href;
  return `/pdfjs/web/viewer.html?file=${encodeURIComponent(file)}`;
}
