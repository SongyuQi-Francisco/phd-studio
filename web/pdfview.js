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
    globalThis.__studioPdf = this; // handy for debugging in the console
    this.eventBus.on("pagechanging", (e) => onPage?.(e.pageNumber));
    this.eventBus.on("scalechanging", (e) => this.onScale?.(e.scale));
    this.eventBus.on("pagesinit", () => {
      this.viewer.currentScaleValue = this.initialScale || "page-width";
      if (this.initialPage > 1) this.viewer.currentPageNumber = this.initialPage;
      this._ready?.();
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
    this.ready = new Promise((r) => (this._ready = r)); // resolves once pages are laid out
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

  // "5.4.1" -> the outline entry titled "5.4.1 …" / "5. …"; false when there is no outline or no match.
  async goToSection(num) {
    if (!this.doc) return false;
    await this.ready;
    const want = String(num).replace(/\.$/, "");
    const re = new RegExp("^\\s*(?:§\\s*)?" + want.replace(/\./g, "\\.") + "(?:\\.?\\s|\\.?$)");
    const walk = (items) => {
      for (const o of items || []) {
        if (re.test(o.title || "")) return o;
        const hit = walk(o.items);
        if (hit) return hit;
      }
      return null;
    };
    const outline = await this.outline();
    const hit = walk(outline);
    if (hit?.dest) { await this.linkService.goToDestination(hit.dest); return true; }
    // No numbered outline entry: find the heading line "5.4.1 Title" in the page text.
    const head = new RegExp("(?:^|\\n)\\s*" + want.replace(/\./g, "\\.") + "\\.?\\s+([A-Z][^\\n]*)");
    for (let n = 1; n <= this.doc.numPages; n++) {
      const tc = await (await this.doc.getPage(n)).getTextContent();
      const m = head.exec(tc.items.map((i) => i.str + (i.hasEOL ? "\n" : "")).join(""));
      if (!m) continue;
      // Outlines often carry the bare title ("Validation protocol"): use it to land on the heading itself.
      const title = m[1].trim().toLowerCase();
      const same = (function find(items) {
        for (const o of items || []) { if ((o.title || "").trim().toLowerCase() === title) return o; const h = find(o.items); if (h) return h; }
        return null;
      })(outline);
      if (same?.dest) await this.linkService.goToDestination(same.dest); else this.goTo(n);
      return true;
    }
    return false;
  }

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
