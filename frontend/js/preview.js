/**
 * Spreadsheet Interactive Review Screen Handler
 */
const PreviewManager = {
  currentJob: null,
  historyStack: [],

  renderTable(data, isBatch = false) {
    this.currentJob = data;
    this.isBatch = isBatch || !!data.batch_id;
    this.historyStack = [JSON.parse(JSON.stringify(data.table_data))];

    // Hide hero & upload card, show review section
    document.getElementById('hero-section').style.display = 'none';
    document.getElementById('upload-section').style.display = 'none';
    const reviewSection = document.getElementById('review-section');
    reviewSection.style.display = 'block';

    // Update Summary Card
    if (this.isBatch) {
      document.getElementById('sum-filename').textContent = data.out_filename || 'medical_reports_batch.xlsx';
      document.getElementById('sum-pages').textContent = `${data.total_files || data.table_data.length} PDFs`;
      document.getElementById('sum-rows').textContent = data.table_data.length;
      document.getElementById('sum-cols').textContent = data.headers.length;
      document.getElementById('sum-ocr').textContent = 'RapidOCR (ONNX)';
      document.getElementById('sum-confidence').textContent = '95.0%';
      
      const qualityBadge = document.getElementById('sum-quality');
      const hasErrors = data.failed_files && data.failed_files > 0;
      qualityBadge.textContent = hasErrors ? `${data.completed_files} OK / ${data.failed_files} Failed` : 'Batch Ready';
      qualityBadge.className = `quality-badge ${hasErrors ? 'quality-review' : 'quality-high'}`;
    } else {
      document.getElementById('sum-filename').textContent = data.filename;
      document.getElementById('sum-pages').textContent = data.pages;
      document.getElementById('sum-rows').textContent = data.rows;
      document.getElementById('sum-cols').textContent = data.columns;
      document.getElementById('sum-ocr').textContent = data.extraction_method || 'RapidOCR';
      document.getElementById('sum-confidence').textContent = `${data.confidence}%`;
      
      const qualityBadge = document.getElementById('sum-quality');
      qualityBadge.textContent = data.data_quality_label;
      qualityBadge.className = `quality-badge ${data.confidence >= 90 ? 'quality-high' : 'quality-review'}`;
    }

    // Build Spreadsheet Grid
    this.buildGrid();

    // Attach Toolbar Event Listeners
    this.initToolbar();

    // Scroll to review section smoothly
    reviewSection.scrollIntoView({ behavior: 'smooth' });
  },

  buildGrid() {
    const data = this.currentJob;
    const tableHeader = document.getElementById('spreadsheet-header');
    const tableBody = document.getElementById('spreadsheet-body');

    if (!tableHeader || !tableBody) return;

    // Header Row: Row #, Col A, B, C...
    let headerHTML = '<tr><th class="row-num">#</th>';
    data.headers.forEach((h, i) => {
      const colLetter = String.fromCharCode(65 + i);
      headerHTML += `<th><div>${colLetter}</div><div style="font-size: 0.75rem; opacity: 0.8; font-weight: normal;">${h}</div></th>`;
    });
    headerHTML += '</tr>';
    tableHeader.innerHTML = headerHTML;

    // Data Rows
    this.renderBodyRows();
  },

  renderBodyRows() {
    const tableBody = document.getElementById('spreadsheet-body');
    const rows = this.currentJob.table_data;
    const lowConf = this.currentJob.low_conf_cells || [];

    let bodyHTML = '';
    rows.forEach((row, rIdx) => {
      bodyHTML += `<tr data-row="${rIdx}">`;
      bodyHTML += `<td class="row-num">${rIdx + 1}</td>`;

      row.forEach((cellVal, cIdx) => {
        const isLowConf = lowConf[rIdx] && lowConf[rIdx][cIdx];
        const lowConfClass = isLowConf ? 'cell-low-conf' : '';
        const titleAttr = isLowConf ? 'title="Low OCR confidence - please verify"' : '';

        bodyHTML += `<td contenteditable="true" 
                         data-row="${rIdx}" 
                         data-col="${cIdx}" 
                         class="${lowConfClass}" 
                         ${titleAttr}>${cellVal || ''}</td>`;
      });

      bodyHTML += '</tr>';
    });

    tableBody.innerHTML = bodyHTML;

    // Listen for cell edits
    tableBody.querySelectorAll('td[contenteditable="true"]').forEach(cell => {
      cell.addEventListener('blur', (e) => {
        const r = parseInt(e.target.dataset.row);
        const c = parseInt(e.target.dataset.col);
        const newVal = e.target.textContent.trim();

        if (this.currentJob.table_data[r][c] !== newVal) {
          this.currentJob.table_data[r][c] = newVal;
          this.saveState();
        }
      });
    });
  },

  initToolbar() {
    const searchInput = document.getElementById('search-input');
    const addRowBtn = document.getElementById('add-row-btn');
    const deleteRowBtn = document.getElementById('delete-row-btn');
    const addColBtn = document.getElementById('add-col-btn');
    const undoBtn = document.getElementById('undo-btn');
    const downloadBtn = document.getElementById('download-btn');

    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        const term = e.target.value.toLowerCase();
        const trs = document.querySelectorAll('#spreadsheet-body tr');
        trs.forEach(tr => {
          const text = tr.textContent.toLowerCase();
          tr.style.display = text.includes(term) ? '' : 'none';
        });
      });
    }

    if (addRowBtn) {
      addRowBtn.onclick = () => {
        const colCount = this.currentJob.headers.length;
        const emptyRow = new Array(colCount).fill('');
        this.currentJob.table_data.push(emptyRow);
        document.getElementById('sum-rows').textContent = this.currentJob.table_data.length;
        this.saveState();
        this.renderBodyRows();
        UI.showToast('Row added at the bottom.', 'info');
      };
    }

    if (deleteRowBtn) {
      deleteRowBtn.onclick = () => {
        if (this.currentJob.table_data.length === 0) return;
        this.currentJob.table_data.pop();
        document.getElementById('sum-rows').textContent = this.currentJob.table_data.length;
        this.saveState();
        this.renderBodyRows();
        UI.showToast('Last row deleted.', 'info');
      };
    }

    if (addColBtn) {
      addColBtn.onclick = () => {
        const newColName = prompt('Enter new column header name:', 'Custom Field');
        if (newColName) {
          this.currentJob.headers.push(newColName);
          this.currentJob.table_data.forEach(r => r.push(''));
          document.getElementById('sum-cols').textContent = this.currentJob.headers.length;
          this.saveState();
          this.buildGrid();
          UI.showToast(`Column "${newColName}" added.`, 'info');
        }
      };
    }

    if (undoBtn) {
      undoBtn.onclick = () => {
        if (this.historyStack.length > 1) {
          this.historyStack.pop();
          const prev = JSON.parse(JSON.stringify(this.historyStack[this.historyStack.length - 1]));
          this.currentJob.table_data = prev;
          document.getElementById('sum-rows').textContent = prev.length;
          this.renderBodyRows();
          UI.showToast('Undo successful.', 'info');
        } else {
          UI.showToast('Nothing to undo.', 'info');
        }
      };
    }

    if (downloadBtn) {
      downloadBtn.onclick = async () => {
        UI.showToast('Preparing Excel file for download...', 'info');

        try {
          if (this.isBatch && this.currentJob.batch_id) {
            // Push updated edited table data to batch export endpoint
            const res = await fetch(`${API_BASE_URL}/api/batch-export/${this.currentJob.batch_id}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                headers: this.currentJob.headers,
                table_data: this.currentJob.table_data
              })
            });

            if (res.ok) {
              window.location.href = `${API_BASE_URL}/api/batch-download/${this.currentJob.batch_id}`;
              UI.showToast('Batch Excel download started!', 'success');
            } else {
              throw new Error('Batch export failed');
            }
          } else {
            // Push updated edited table data to backend export endpoint
            const res = await fetch(`${API_BASE_URL}/api/export/${this.currentJob.job_id}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                headers: this.currentJob.headers,
                table_data: this.currentJob.table_data
              })
            });

            if (res.ok) {
              // Trigger browser file download
              window.location.href = `${API_BASE_URL}/api/download/${this.currentJob.job_id}`;
              UI.showToast('Download started!', 'success');
            } else {
              throw new Error('Export failed');
            }
          }
        } catch (err) {
          UI.showToast('Error downloading file.', 'error');
        }
      };
    }
  },

  saveState() {
    this.historyStack.push(JSON.parse(JSON.stringify(this.currentJob.table_data)));
    if (this.historyStack.length > 20) this.historyStack.shift();
  }
};
