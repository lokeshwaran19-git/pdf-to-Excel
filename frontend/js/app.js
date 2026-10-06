/**
 * PDF to Excel Core Application Initialization
 */

// ─── API Configuration ─────────────────────────────────────────────────────
// When frontend is served from a different domain (e.g. Cloudflare Workers),
// set this to your Render backend URL. Leave empty string '' to use same-origin.
const RENDER_BACKEND_URL = 'https://pdf-to-excel-n2ho.onrender.com';

// Auto-detect:
// - On Render (onrender.com): use same-origin (empty string)
// - On localhost / 127.0.0.1: use same-origin (empty string) — local FastAPI serves frontend
// - Any other remote host (e.g. Cloudflare Workers): use Render backend URL
const _host = window.location.hostname;
const API_BASE_URL = (_host === 'localhost' || _host === '127.0.0.1' || _host.includes('onrender.com'))
  ? ''
  : RENDER_BACKEND_URL.replace(/\/+$/, '');
// ───────────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  console.log('PDF to Excel SaaS Application Initialized.');

  const hamburgerBtn = document.getElementById('hamburger-btn');
  const mobileNav    = document.getElementById('mobile-nav');

  const closeMobileNav = () => {
    if (mobileNav) mobileNav.classList.remove('open');
    if (hamburgerBtn) {
      hamburgerBtn.classList.remove('active');
      hamburgerBtn.setAttribute('aria-expanded', 'false');
    }
  };

  // ── Smooth scroll for anchor nav links ──────────────────
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
      const targetId = this.getAttribute('href');
      if (targetId === '#') return;

      const targetEl = document.querySelector(targetId);
      if (targetEl) {
        e.preventDefault();
        targetEl.scrollIntoView({ behavior: 'smooth' });
      }

      closeMobileNav();
    });
  });

  // ── Convert PDF buttons in header / mobile drawer → scroll to upload ──
  const convertBtns = document.querySelectorAll('#convert-header-btn, #convert-mobile-btn');
  convertBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const uploadCard = document.getElementById('upload-section');
      if (uploadCard) uploadCard.scrollIntoView({ behavior: 'smooth' });
      closeMobileNav();
    });
  });

  // ── Hamburger mobile menu toggle ─────────────────────────
  if (hamburgerBtn && mobileNav) {
    hamburgerBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = mobileNav.classList.toggle('open');
      hamburgerBtn.classList.toggle('active', isOpen);
      hamburgerBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });

    // Close on outside click
    document.addEventListener('click', (e) => {
      if (!hamburgerBtn.contains(e.target) && !mobileNav.contains(e.target)) {
        closeMobileNav();
      }
    });
  }

  // ── Mode Switcher (Single Table vs Batch Medical) ─────────
  const tabSingle = document.getElementById('tab-single-mode');
  const tabBatch  = document.getElementById('tab-batch-mode');
  const singleContainer = document.getElementById('single-mode-container');
  const batchContainer  = document.getElementById('batch-mode-container');

  if (tabSingle && tabBatch && singleContainer && batchContainer) {
    tabSingle.addEventListener('click', () => {
      tabSingle.classList.add('active');
      tabSingle.setAttribute('aria-selected', 'true');
      tabBatch.classList.remove('active');
      tabBatch.setAttribute('aria-selected', 'false');

      singleContainer.style.display = 'block';
      batchContainer.style.display  = 'none';
    });

    tabBatch.addEventListener('click', () => {
      tabBatch.classList.add('active');
      tabBatch.setAttribute('aria-selected', 'true');
      tabSingle.classList.remove('active');
      tabSingle.setAttribute('aria-selected', 'false');

      batchContainer.style.display  = 'block';
      singleContainer.style.display = 'none';
    });
  }
});
