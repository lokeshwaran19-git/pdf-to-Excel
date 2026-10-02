/**
 * PDF to Excel Core Application Initialization
 */

// ─── API Configuration ─────────────────────────────────────────────────────
// When frontend is served from a different domain (e.g. Cloudflare Workers),
// set this to your Render backend URL. Leave empty string '' to use same-origin.
const RENDER_BACKEND_URL = 'https://pdf-to-excel-n2h0.onrender.com';

// Auto-detect:
// - On Render (onrender.com): use same-origin (empty string)
// - On localhost / 127.0.0.1: use same-origin (empty string) — local FastAPI serves frontend
// - Any other remote host: use Render backend URL
const _host = window.location.hostname;
const API_BASE_URL = (_host === 'localhost' || _host === '127.0.0.1' || _host.includes('onrender.com'))
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
