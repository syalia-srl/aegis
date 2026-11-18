// Mozilla's PDF viewer (pdf.js), served from /pdfjs. The file URL has to be
// absolute and on this origin: the viewer refuses a file from anywhere else.

export function pdfViewerSrc(fileUrl) {
  const file = new URL(fileUrl, location.href).href;
  return `/pdfjs/web/viewer.html?file=${encodeURIComponent(file)}`;
}
