/**
 * PDF Upload & Conversion Handler Module
 */
const UploadManager = {
  selectedFile: null,

  init() {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const removeBtn = document.getElementById('remove-file-btn');
    const convertBtn = document.getElementById('convert-btn');

    if (!dropZone || !fileInput) return;

    // Drag & Drop events
    ['dragenter', 'dragover'].forEach(eventName => {
      dropZone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.add('dragover');
      });
    });

    ['dragleave', 'drop'].forEach(eventName => {
      dropZone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.remove('dragover');
      });
    });

    dropZone.addEventListener('drop', (e) => {
      const files = e.dataTransfer.files;
      if (files.length > 0) {
        this.handleFileSelect(files[0]);
      }
    });

    dropZone.addEventListener('click', () => {
      fileInput.click();
    });

    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length > 0) {
        this.handleFileSelect(e.target.files[0]);
      }
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

    // Display file card details
    document.getElementById('file-name').textContent = file.name;
    document.getElementById('file-size').textContent = UI.formatBytes(file.size);
    document.getElementById('file-card').style.display = 'flex';
    document.getElementById('convert-btn').disabled = false;

    UI.showToast(`Selected file: ${file.name}`, 'info');
  },

  resetUpload() {
    this.selectedFile = null;
    document.getElementById('file-input').value = '';
    document.getElementById('file-card').style.display = 'none';
    document.getElementById('progress-container').style.display = 'none';
    document.getElementById('convert-btn').disabled = true;
  },

  async startConversion() {
    if (!this.selectedFile) return;

    const progressContainer = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar-fill');
    const progressStatus = document.getElementById('progress-status');
    const convertBtn = document.getElementById('convert-btn');

    progressContainer.style.display = 'block';
    convertBtn.disabled = true;

    // Real processing stage steps
    const stages = [
      { pct: 15, text: "Uploading PDF document..." },
      { pct: 30, text: "Analyzing PDF page orientation..." },
      { pct: 45, text: "Rendering pages & preprocessing contrast..." },
      { pct: 60, text: "Running PaddleOCR engine..." },
      { pct: 75, text: "Detecting table boundaries & columns..." },
      { pct: 85, text: "Reconstructing rows & cell fields..." },
      { pct: 95, text: "Validating data & generating Excel file..." }
    ];

    let currentStage = 0;
    const interval = setInterval(() => {
      if (currentStage < stages.length) {
        const stage = stages[currentStage];
        progressBar.style.width = `${stage.pct}%`;
        progressStatus.textContent = stage.text;
        currentStage++;
      }
    }, 600);

    const formData = new FormData();
    formData.append("file", this.selectedFile);

    try {
      const response = await fetch(`${API_BASE_URL}/api/convert`, {
        method: 'POST',
        body: formData
      });

      clearInterval(interval);

      if (!response.ok) {
        const errData = await response.json();
        throw new Error(errData.detail || 'Conversion failed.');
      }

      const result = await response.json();

      progressBar.style.width = '100%';
      progressStatus.textContent = 'Conversion Complete!';

      UI.showToast('Table extracted successfully!', 'success');

      setTimeout(() => {
        // Load interactive spreadsheet preview
        PreviewManager.renderTable(result);
      }, 500);

    } catch (err) {
      clearInterval(interval);
      progressStatus.textContent = 'Error during processing.';
      UI.showToast(err.message || 'An error occurred during extraction.', 'error');
      convertBtn.disabled = false;
    }
  }
};

document.addEventListener('DOMContentLoaded', () => {
  UploadManager.init();
});
