/**
 * PDF Upload & Conversion Handler Module
 * API contract is unchanged — only UI/UX layer is modified.
 */
const UploadManager = {
  selectedFile: null,

  init() {
    const dropZone    = document.getElementById('drop-zone');
    const fileInput   = document.getElementById('file-input');
    const removeBtn   = document.getElementById('remove-file-btn');
    const convertBtn  = document.getElementById('convert-btn');
    const tryAgainBtn = document.getElementById('try-again-btn');

    if (!dropZone || !fileInput) return;

    // ── Drag & Drop events ────────────────────────────────
    ['dragenter', 'dragover'].forEach(evt => {
      dropZone.addEventListener(evt, (e) => {
        e.preventDefault(); e.stopPropagation();
        dropZone.classList.add('dragover');
      });
    });

    ['dragleave', 'drop'].forEach(evt => {
      dropZone.addEventListener(evt, (e) => {
        e.preventDefault(); e.stopPropagation();
        dropZone.classList.remove('dragover');
      });
    });

    dropZone.addEventListener('drop', (e) => {
      const files = e.dataTransfer.files;
      if (files.length > 0) this.handleFileSelect(files[0]);
    });

    dropZone.addEventListener('click', () => fileInput.click());

    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length > 0) this.handleFileSelect(e.target.files[0]);
    });

    if (removeBtn) {
      removeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.resetUpload();
      });
    }

    if (convertBtn) {
      convertBtn.addEventListener('click', () => {
        if (this.selectedFile) {
          this.startConversion();
        } else {
          UI.showToast('Please select a PDF file first.', 'error');
        }
      });
    }

    if (tryAgainBtn) {
      tryAgainBtn.addEventListener('click', () => this.resetToUpload());
    }
  },

  handleFileSelect(file) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      UI.showToast('Please select a valid PDF file.', 'error');
      return;
    }

    const maxSize = 25 * 1024 * 1024; // 25 MB
    if (file.size > maxSize) {
      UI.showToast('File size exceeds the 25 MB limit.', 'error');
      return;
    }

    this.selectedFile = file;

    // Show file card
    document.getElementById('file-name').textContent = file.name;
    document.getElementById('file-size').textContent = UI.formatBytes(file.size);
    document.getElementById('file-card').style.display = 'flex';
    document.getElementById('convert-btn').disabled = false;

    // Hide any previous result cards
    this._hideResultCards();

    UI.showToast(`Selected: ${file.name}`, 'info');
  },

  resetUpload() {
    this.selectedFile = null;
    document.getElementById('file-input').value = '';
    document.getElementById('file-card').style.display = 'none';
    document.getElementById('convert-btn').disabled = true;
    this._hideResultCards();
  },

  resetToUpload() {
    this._hideResultCards();
    document.getElementById('convert-btn').disabled = this.selectedFile ? false : true;
  },

  _hideResultCards() {
    document.getElementById('conversion-card').style.display = 'none';
    document.getElementById('success-card').style.display = 'none';
    document.getElementById('error-card').style.display = 'none';
  },

  // ── Progress stages displayed in the conversion card UI ─
  // These are frontend-only — they do NOT reflect real backend progress.
  _stages: [
    { pct: 10, stage: 'Uploading PDF document…',              sub: 'Preparing your document…' },
    { pct: 25, stage: 'Rendering pages at high resolution…',  sub: 'Analysing page structure and orientation…' },
    { pct: 42, stage: 'Preprocessing image contrast…',        sub: 'Enhancing image clarity for OCR…' },
    { pct: 58, stage: 'Running AI OCR engine…',               sub: 'Detecting text bounding boxes and cells…' },
    { pct: 72, stage: 'Detecting table boundaries…',          sub: 'Identifying rows, columns, and regions…' },
    { pct: 84, stage: 'Reconstructing rows & cells…',         sub: 'Merging multi-line values and IDs…' },
    { pct: 93, stage: 'Generating Excel spreadsheet…',        sub: 'Validating data and formatting columns…' },
    { pct: 97, stage: 'Almost ready…',                        sub: 'Finalising your Excel file…' },
  ],

  async startConversion() {
    if (!this.selectedFile) return;

    const convertBtn      = document.getElementById('convert-btn');
    const convCard        = document.getElementById('conversion-card');
    const successCard     = document.getElementById('success-card');
    const errorCard       = document.getElementById('error-card');
    const progressFill    = document.getElementById('conv-progress-fill');
    const stageText       = document.getElementById('conv-stage-text');
    const pctText         = document.getElementById('conv-pct-text');
    const subtitleEl      = document.getElementById('conv-subtitle');

    // ── Set convert button to loading state ──────────────
    convertBtn.disabled = true;
    convertBtn.classList.add('loading');
    convertBtn.querySelector('.btn-convert-text').textContent = 'Converting…';
    // Insert spinner if not present
    if (!convertBtn.querySelector('.btn-spinner')) {
      const sp = document.createElement('span');
      sp.className = 'btn-spinner';
      convertBtn.insertBefore(sp, convertBtn.firstChild);
    }

    // ── Show conversion card, hide result cards ──────────
    this._hideResultCards();
    convCard.style.display = 'block';
    convCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    // ── Animate stage progress (frontend-only UX) ────────
    let stageIdx = 0;
    const setStage = (idx) => {
      const s = this._stages[idx];
      if (!s) return;
      progressFill.style.width = `${s.pct}%`;
      stageText.textContent    = s.stage;
      pctText.textContent      = `${s.pct}%`;
      // Fade subtitle
      subtitleEl.style.opacity = '0';
      setTimeout(() => {
        subtitleEl.textContent = s.sub;
        subtitleEl.style.opacity = '1';
      }, 200);
    };

    setStage(0);
    const stageInterval = setInterval(() => {
      stageIdx++;
      if (stageIdx < this._stages.length) {
        setStage(stageIdx);
      } else {
        // Hold at last stage while waiting
        stageText.textContent = 'AI engine processing (almost ready)…';
      }
    }, 2400);

    // ── Build form data (unchanged API contract) ─────────
    const formData = new FormData();
    formData.append('file', this.selectedFile);

    try {
      let response;
      let retries = 0;
      const maxRetries = 4;

      while (retries <= maxRetries) {
        response = await fetch(`${API_BASE_URL}/api/convert`, {
          method: 'POST',
          body: formData,
        });

        // If server is busy processing another document (concurrency lock), wait and auto-retry
        if ((response.status === 503 || response.status === 429) && retries < maxRetries) {
          retries++;
          stageText.textContent = `Server busy (document in queue). Retrying in 5s… (${retries}/${maxRetries})`;
          subtitleEl.textContent = 'Another conversion is finishing. You are next in queue…';
          await this._delay(5000);
          continue;
        }

        break;
      }

      clearInterval(stageInterval);

      if (!response.ok) {
        let errMsg = `Server error (${response.status}: ${response.statusText || 'Unknown'})`;
        try {
          const errData = await response.json();
          if (errData && errData.detail) errMsg = errData.detail;
        } catch (_) {}
        throw new Error(errMsg);
      }

      const result = await response.json();

      // ── Animate to 100% then show success ────────────────
      progressFill.style.width = '100%';
      pctText.textContent      = '100%';
      stageText.textContent    = 'Conversion Complete!';
      subtitleEl.textContent   = 'Building your spreadsheet…';

      await this._delay(650);

      convCard.style.display = 'none';

      // Populate success card meta
      const meta = [];
      if (result.filename) meta.push(`<strong>${result.filename}</strong>`);
      if (result.pages)    meta.push(`${result.pages} page${result.pages > 1 ? 's' : ''} processed`);
      if (result.rows)     meta.push(`${result.rows} rows extracted`);
      document.getElementById('success-meta').innerHTML =
        meta.length ? meta.join(' · ') : 'Your Excel file is ready.';

      successCard.style.display = 'block';
      UI.showToast('Table extracted successfully!', 'success');

      await this._delay(900);
      successCard.style.display = 'none';

      // ── Render spreadsheet preview (unchanged) ───────────
      PreviewManager.renderTable(result);

    } catch (err) {
      clearInterval(stageInterval);

      convCard.style.display = 'none';

      // Show error card
      document.getElementById('error-msg').textContent =
        err.message || 'We couldn\'t convert your PDF. Please try again.';
      errorCard.style.display = 'block';

      UI.showToast(err.message || 'An error occurred during extraction.', 'error');

    } finally {
      // Always restore convert button
      convertBtn.classList.remove('loading');
      convertBtn.disabled = false;
      convertBtn.querySelector('.btn-convert-text').textContent = 'Convert PDF';
    }
  },

  _delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  },
};

document.addEventListener('DOMContentLoaded', () => {
  UploadManager.init();
});
