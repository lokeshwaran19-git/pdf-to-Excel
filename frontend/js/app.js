/**
 * PDF to Excel Core Application Initialization
 */
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
