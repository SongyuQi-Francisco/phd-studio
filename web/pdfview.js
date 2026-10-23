// Thin wrapper around pdf.js's own PDFViewer: continuous scroll, text layer,
// internal links, find, outline. Adds rectangle capture for figures/formulas.
import * as pdfjsLib from "/vendor/pdfjs/pdf.min.mjs";

globalThis.pdfjsLib = pdfjsLib;
pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.min.mjs";
const { EventBus, PDFViewer, PDFLinkService, PDFFindController } = await import("/vendor/pdfjs/pdf_viewer.mjs");

export class PdfView {
  constructor(container, { onPage, onPages, onReady }) {
    this.container = container;
    this.eventBus = new EventBus();
    this.linkService = new PDFLinkService({ eventBus: this.eventBus });
    this.findController = new PDFFindController({ eventBus: this.eventBus, linkService: this.linkService });
    this.viewer = new PDFViewer({
      container,
      viewer: container.querySelector(".pdfViewer"),
      eventBus: this.eventBus,
      linkService: this.linkService,
      findController: this.findController,
      removePageBorders: true,
    });
    this.linkService.setViewer(this.viewer);
    this.eventBus.on("pagechanging", (e) => onPage?.(e.pageNumber));
    this.eventBus.on("scalechanging", (e) => this.onScale?.(e.scale));
    this.eventBus.on("pagesinit", () => {
      this.viewer.currentScaleValue = this.initialScale || "page-width";
      if (this.initialPage > 1) this.viewer.currentPageNumber = this.initialPage;
      onReady?.();
    });
    this.onPages = onPages;
    this.doc = null;
    this._resize = new ResizeObserver(() => {
      const v = this.viewer.currentScaleValue;
      if (v === "page-width" || v === "page-fit") this.viewer.currentScaleValue = v;
    });
    this._resize.observe(container);
  }

  async open(url, { page = 1, scale } = {}) {
    this.initialPage = page;
    this.initialScale = scale;
    const task = pdfjsLib.getDocument({
      url,
      cMapUrl: "/vendor/pdfjs/cmaps/",
      cMapPacked: true,
      standardFontDataUrl: "/vendor/pdfjs/standard_fonts/",
    });
    const doc = await task.promise;
    if (this.doc) await this.doc.destroy();
    this.doc = doc;
    this.viewer.setDocument(doc);
    this.linkService.setDocument(doc);
    this.onPages?.(doc.numPages);
    return doc;
  }

  get page() { return this.viewer.currentPageNumber; }
  goTo(n) { if (this.doc && n >= 1 && n <= this.doc.numPages) this.viewer.currentPageNumber = n; }
  zoom(factor) { this.viewer.currentScale = Math.min(4, Math.max(0.4, this.viewer.currentScale * factor)); }
  fitWidth() { this.viewer.currentScaleValue = "page-width"; }
  get scaleValue() { return this.viewer.currentScaleValue; }

  async outline() {
    if (!this.doc) return [];
    return (await this.doc.getOutline()) || [];
  }
  goToDest(dest) { this.linkService.goToDestination(dest); }

  find(query, previous = false) {
    this.eventBus.dispatch("find", {
      source: this, type: previous ? "again" : "", query, caseSensitive: false,
      entireWord: false, highlightAll: true, findPrevious: previous, matchDiacritics: false,
    });
  }

  // Crop a client-space rectangle out of whichever rendered page it falls on.
  capture(rect) {
    for (const pageEl of this.container.querySelectorAll(".page")) {
      const canvas = pageEl.querySelector("canvas");
      if (!canvas) continue;
      const box = canvas.getBoundingClientRect();
      const x0 = Math.max(rect.left, box.left), y0 = Math.max(rect.top, box.top);
      const x1 = Math.min(rect.right, box.right), y1 = Math.min(rect.bottom, box.bottom);
      if (x1 - x0 < 8 || y1 - y0 < 8) continue;
      const sx = canvas.width / box.width, sy = canvas.height / box.height;
      const out = document.createElement("canvas");
      out.width = Math.round((x1 - x0) * sx);
      out.height = Math.round((y1 - y0) * sy);
      out.getContext("2d").drawImage(canvas, (x0 - box.left) * sx, (y0 - box.top) * sy,
        out.width, out.height, 0, 0, out.width, out.height);
      return { dataUrl: out.toDataURL("image/png"), page: Number(pageEl.dataset.pageNumber) };
    }
    return null;
  }

  destroy() {
    this._resize.disconnect();
    this.doc?.destroy();
  }
}
