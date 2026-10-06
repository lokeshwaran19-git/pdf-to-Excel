/**
 * PDF to Excel Core Application Initialization
 */

// ─── API Configuration ─────────────────────────────────────────────────────
// The Cloudflare Worker proxies all /api/* requests to the Render backend.
// So from the browser's perspective, the API is always same-origin — no CORS.
// On localhost the local FastAPI server serves /api/* directly (also same-origin).
const API_BASE_URL = ''; // always same-origin — Worker handles the proxy
// ───────────────────────────────────────────────────────────────────────────


// ── Silent Render warmup ping ───────────────────────────────────────────────
// Render free tier sleeps after 15 min of inactivity. Cold-start takes 30-60s.
// Pinging /api/health on page load wakes Render before the user hits Convert.
// The Cloudflare Worker proxies the request — no CORS needed.
function _warmupRenderServer() {
  fetch('/api/health')
    .then(r => r.ok && console.log('[warmup] Render server is awake \u2713'))
    .catch(() => {
      // Server still cold — retry once after 20s
      setTimeout(() => fetch('/api/health').catch(() => {}), 20000);
    });
}


document.addEventListener('DOMContentLoaded', () => {
  console.log('PDF to Excel SaaS Application Initialized.');
  _warmupRenderServer(); // fire-and-forget warmup — runs in background

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
