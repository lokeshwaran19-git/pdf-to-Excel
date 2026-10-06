/**
 * Batch Medical Report Upload & Conversion Handler Module
 * Manages 1 to 10 medical PDF uploads and asynchronous batch processing.
 */
const BatchUploadManager = {
  selectedFiles: [],
  maxFiles: 10,
  maxSizeBytes: 25 * 1024 * 1024, // 25 MB

  init() {
    const dropZone = document.getElementById('batch-drop-zone');
    const fileInput = document.getElementById('batch-file-input');
    const removeAllBtn = document.getElementById('batch-remove-all-btn');
    const convertBtn = document.getElementById('batch-convert-btn');

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
      const files = Array.from(e.dataTransfer.files);
      if (files.length > 0) this.handleFilesAdd(files);
    });

    dropZone.addEventListener('click', () => fileInput.click());

    fileInput.addEventListener('change', (e) => {
      const files = Array.from(e.target.files);
      if (files.length > 0) this.handleFilesAdd(files);
    });

    if (removeAllBtn) {
      removeAllBtn.addEventListener('click', () => this.clearAllFiles());
    }

    if (convertBtn) {
      convertBtn.addEventListener('click', () => {
        if (this.selectedFiles.length > 0) {
          this.startBatchConversion();
        } else {
          UI.showToast('Please select at least 1 medical PDF file.', 'error');
        }
      });
    }
  },

  handleFilesAdd(newFiles) {
    let addedCount = 0;
    for (const f of newFiles) {
      if (this.selectedFiles.length >= this.maxFiles) {
        UI.showToast(`Maximum ${this.maxFiles} files allowed.`, 'error');
        break;
      }

      if (!f.name.toLowerCase().endsWith('.pdf')) {
        UI.showToast(`Skipped non-PDF file: ${f.name}`, 'error');
        continue;
      }

      if (f.size > this.maxSizeBytes) {
        UI.showToast(`Skipped ${f.name}: Exceeds 25 MB limit.`, 'error');
        continue;
      }

      // Check duplicates
      const exists = this.selectedFiles.some(
        existing => existing.name === f.name && existing.size === f.size
      );
      if (exists) continue;

      this.selectedFiles.push(f);
      addedCount++;
    }

    if (addedCount > 0) {
      UI.showToast(`Added ${addedCount} PDF${addedCount > 1 ? 's' : ''}`, 'info');
    }

    this.renderFileList();
  },

  removeFile(index) {
    if (index >= 0 && index < this.selectedFiles.length) {
      const removed = this.selectedFiles.splice(index, 1);
      this.renderFileList();
      if (removed.length > 0) {
        UI.showToast(`Removed ${removed[0].name}`, 'info');
      }
    }
  },

  clearAllFiles() {
    this.selectedFiles = [];
    const fileInput = document.getElementById('batch-file-input');
    if (fileInput) fileInput.value = '';
    this.renderFileList();
  },

  renderFileList() {
    const card = document.getElementById('batch-file-card');
    const listEl = document.getElementById('batch-file-list');
    const countEl = document.getElementById('batch-file-count');
    const convertBtn = document.getElementById('batch-convert-btn');

    if (!card || !listEl || !countEl || !convertBtn) return;

    countEl.textContent = this.selectedFiles.length;

    if (this.selectedFiles.length === 0) {
      card.style.display = 'none';
      convertBtn.disabled = true;
      listEl.innerHTML = '';
      return;
    }

    card.style.display = 'flex';
    convertBtn.disabled = false;

    let html = '';
    this.selectedFiles.forEach((f, idx) => {
      html += `
        <div class="batch-file-item">
          <div class="batch-file-left">
            <span class="batch-file-check">✓</span>
            <div>
              <div class="batch-file-name" title="${f.name}">${f.name}</div>
              <div class="batch-file-size">${UI.formatBytes(f.size)}</div>
            </div>
          </div>
          <button class="batch-file-remove" onclick="BatchUploadManager.removeFile(${idx})" title="Remove file">✕</button>
        </div>
      `;
    });

    listEl.innerHTML = html;
  },

  async startBatchConversion() {
    if (this.selectedFiles.length === 0) return;

    const convertBtn = document.getElementById('batch-convert-btn');
    const convCard = document.getElementById('conversion-card');
    const progressFill = document.getElementById('conv-progress-fill');
    const stageText = document.getElementById('conv-stage-text');
    const pctText = document.getElementById('conv-pct-text');
    const subtitleEl = document.getElementById('conv-subtitle');

    // Button loading state
    convertBtn.disabled = true;
    convertBtn.classList.add('loading');
    convertBtn.querySelector('.btn-convert-text').textContent = 'Converting Batch…';

    // Show conversion card
    convCard.style.display = 'block';
    convCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    progressFill.style.width = '8%';
    pctText.textContent = '8%';
    stageText.textContent = `Uploading ${this.selectedFiles.length} medical PDFs…`;
    subtitleEl.textContent = 'Connecting to asynchronous batch queue…';

    const formData = new FormData();
    this.selectedFiles.forEach(file => {
      formData.append('files', file);
    });

    let pollInterval = null;

    try {
      // 1. Submit batch job (immediate response in <150ms)
      const res = await fetch(`${API_BASE_URL}/api/batch-convert`, {
        method: 'POST',
        body: formData
      });

      if (!res.ok) {
        let errMsg = `Server error (${res.status})`;
        try {
          const errData = await res.json();
          if (errData && errData.detail) errMsg = errData.detail;
        } catch (_) {}
        throw new Error(errMsg);
      }

      const initData = await res.json();
      const batchId = initData.batch_id;
      if (!batchId) throw new Error('Did not receive a valid Batch ID from server.');

      // 2. Poll batch status
      const pollBatch = async () => {
        try {
          const sRes = await fetch(`${API_BASE_URL}/api/batch-status/${batchId}?_t=${Date.now()}`);
          if (!sRes.ok) return;

          const data = await sRes.json();

          if (data.status === 'queued') {
            stageText.textContent = 'Batch queued for processing…';
            subtitleEl.textContent = 'Waiting in secure conversion queue…';
            progressFill.style.width = '12%';
            pctText.textContent = '12%';
          } else if (data.status === 'processing') {
            const prog = Math.max(15, data.progress || 20);
            progressFill.style.width = `${prog}%`;
            pctText.textContent = `${prog}%`;
            stageText.textContent = data.stage || `Processing batch (${data.completed_files} of ${data.total_files} done)…`;
            subtitleEl.textContent = data.current_file 
              ? `Scanning all pages of ${data.current_file} for Patient Info, Assessments & Visit Code…`
              : 'Extracting medical sections across pages…';
          } else if (data.status === 'completed') {
            if (pollInterval) {
              clearInterval(pollInterval);
              pollInterval = null;
            }
            await this._handleBatchSuccess(data.result);
          } else if (data.status === 'failed') {
            if (pollInterval) {
              clearInterval(pollInterval);
              pollInterval = null;
            }
            throw new Error(data.error || 'Batch conversion failed. Please try again.');
          }
        } catch (pollErr) {
          if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
          }
          this._handleBatchError(pollErr);
        }
      };

      await pollBatch();
      pollInterval = setInterval(pollBatch, 2200);

    } catch (err) {
      if (pollInterval) clearInterval(pollInterval);
      this._handleBatchError(err);
    } finally {
      convertBtn.classList.remove('loading');
      convertBtn.disabled = false;
      convertBtn.querySelector('.btn-convert-text').textContent = 'Convert All to Excel';
    }
  },

  async _handleBatchSuccess(result) {
    const convCard = document.getElementById('conversion-card');
    const successCard = document.getElementById('success-card');
    const progressFill = document.getElementById('conv-progress-fill');
    const stageText = document.getElementById('conv-stage-text');
    const pctText = document.getElementById('conv-pct-text');
    const subtitleEl = document.getElementById('conv-subtitle');

    progressFill.style.width = '100%';
    pctText.textContent = '100%';
    stageText.textContent = 'Batch Conversion Complete!';
    subtitleEl.textContent = 'Generating combined Excel review spreadsheet…';

    await new Promise(r => setTimeout(r, 650));
    convCard.style.display = 'none';

    // Populate success card
    const meta = [
      `<strong>${result.total_files} Medical PDFs processed</strong>`,
      `${result.completed_files} successfully converted into ONE Excel file`,
      `${result.table_data ? result.table_data.length : 0} total records`
    ];
    if (result.failed_files > 0) {
      meta.push(`<span style="color:var(--danger)">${result.failed_files} file(s) failed</span>`);
    }

    document.getElementById('success-meta').innerHTML = meta.join(' · ');
    successCard.style.display = 'block';
    UI.showToast(`Batch extraction complete! ${result.completed_files} records created.`, 'success');

    await new Promise(r => setTimeout(r, 900));
    successCard.style.display = 'none';

    // Render in PreviewManager review grid
    if (result) {
      PreviewManager.renderTable(result, true);
    }
  },

  _handleBatchError(err) {
    const convCard = document.getElementById('conversion-card');
    const errorCard = document.getElementById('error-card');

    convCard.style.display = 'none';
    const errMsg = err.message || 'We could not convert the batch. Please try again.';
    document.getElementById('error-msg').textContent = errMsg;
    errorCard.style.display = 'block';
    UI.showToast(errMsg, 'error');
  }
};

document.addEventListener('DOMContentLoaded', () => {
  BatchUploadManager.init();
});
