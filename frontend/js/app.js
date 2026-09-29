/**
 * PDF to Excel Core Application Initialization
 */

// ─── API Configuration ─────────────────────────────────────────────────────
// When frontend is served from a different domain (e.g. Cloudflare Workers),
// set this to your Render backend URL. Leave empty string '' to use same-origin.
const RENDER_BACKEND_URL = 'https://pdf-to-excel-n2ho.onrender.com';

// Auto-detect: if we're NOT on the Render domain, use the full Render URL
const API_BASE_URL = window.location.hostname.includes('onrender.com')
  ? ''
  : RENDER_BACKEND_URL;
// ───────────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  console.log('PDF to Excel SaaS Application Initialized.');

  // Smooth scroll for nav links
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
      e.preventDefault();
      const targetId = this.getAttribute('href');
      if (targetId === '#') return;

      const targetEl = document.querySelector(targetId);
      if (targetEl) {
        targetEl.scrollIntoView({ behavior: 'smooth' });
      }
    });
  });

  // Convert PDF button in header
  const convertHeaderBtn = document.getElementById('convert-header-btn');
  if (convertHeaderBtn) {
    convertHeaderBtn.addEventListener('click', () => {
      const uploadCard = document.getElementById('upload-section');
      if (uploadCard) {
        uploadCard.scrollIntoView({ behavior: 'smooth' });
      }
    });
  }
});
