/**
 * RUNECLAW icon sprite — the single source for every icon on the site.
 * Injected once on load; pages reference symbols with:
 *   <svg class="icon"><use href="#icon-name"></use></svg>
 * (Replaces the sprite that was duplicated verbatim in index.html and
 * dashboard.html.)
 */
(function () {
  const SPRITE = `
<svg id="icon-sprite" style="display:none" aria-hidden="true">
  <symbol id="brand-mark" viewBox="0 0 32 32">
    <circle cx="16" cy="16" r="12.5" fill="none" stroke="var(--gold)" stroke-width="1.3" opacity=".55"/>
    <circle cx="16" cy="16" r="9" fill="none" stroke="var(--info)" stroke-width="1.1" opacity=".55"/>
    <text x="16" y="21.5" font-family="Georgia,serif" font-size="17" fill="var(--gold-bright)" text-anchor="middle" stroke="none">ᐱ</text>
    <circle cx="16" cy="5.2" r="1.5" fill="var(--up)" stroke="none"/>
  </symbol>
  <symbol id="icon-home" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 11 12 4l8 7"/><path d="M6 10v9h12v-9"/><path d="M10 19v-5h4v5"/></g></symbol>
  <symbol id="icon-radar" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5.3"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><path d="M12 12 18 6"/></g></symbol>
  <symbol id="icon-chart" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V11"/><path d="M10 20V6"/><path d="M16 20V13.5"/><path d="M20 20V4"/></g></symbol>
  <symbol id="icon-shield" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/><path d="M9 12l2 2 4-4"/></g></symbol>
  <symbol id="icon-wallet" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" width="18" height="13" rx="2.2"/><path d="M3 10.5h18"/><circle cx="17" cy="14.7" r="1.1" fill="currentColor" stroke="none"/></g></symbol>
  <symbol id="icon-rocket" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2c3 2 5 6 5 10 0 2-.7 3.8-1.6 5.2L12 22l-3.4-4.8C7.7 15.8 7 14 7 12c0-4 2-8 5-10z"/><circle cx="12" cy="10.5" r="2"/><path d="M8.3 16.6 5.5 19.4M15.7 16.6l2.8 2.8"/></g></symbol>
  <symbol id="icon-globe" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/></g></symbol>
  <symbol id="icon-bolt" viewBox="0 0 24 24"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" fill="currentColor" stroke="none"/></symbol>
  <symbol id="icon-target" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none"/></g></symbol>
  <symbol id="icon-coin" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M9.3 9.3c0-1.3 1.2-2.3 2.7-2.3s2.7 1 2.7 2.3c0 2.5-5.4 1.9-5.4 4.4 0 1.3 1.2 2.3 2.7 2.3s2.7-1 2.7-2.3"/></g></symbol>
  <symbol id="icon-sparkle" viewBox="0 0 24 24"><g fill="currentColor" stroke="none"><path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15z"/></g></symbol>
  <symbol id="icon-cog" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3.2"/><path d="M12 2.8v2.6M12 18.6v2.6M4.1 7.4l2.2 1.3M17.7 15.3l2.2 1.3M4.1 16.6l2.2-1.3M17.7 8.7l2.2-1.3"/></g></symbol>
  <symbol id="icon-user" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8.3" r="3.8"/><path d="M4.5 20.2c1.4-3.4 4.1-5.1 7.5-5.1s6.1 1.7 7.5 5.1"/></g></symbol>
  <symbol id="icon-chat" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5H9l-5 4V6.5z"/><path d="M8.5 9.5h7M8.5 13h4.5"/></g></symbol>
  <symbol id="icon-check" viewBox="0 0 24 24"><path d="M5 12.5 10 17.5 19 7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></symbol>
  <symbol id="icon-alert" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5 21 19.5H3L12 3.5z"/><path d="M12 10v4.5"/><circle cx="12" cy="17" r=".4" fill="currentColor"/></g></symbol>
  <symbol id="icon-offline" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 17.5A4.5 4.5 0 0 1 7 8.6 6 6 0 0 1 18.4 10 4 4 0 0 1 18 17.7"/><path d="M4 4l16 16"/></g></symbol>
  <symbol id="icon-inbox" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v14H4z"/><path d="M4 13h5l1.5 2.5h3L15 13h5"/></g></symbol>
  <symbol id="icon-link" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14a4.6 4.6 0 0 0 6.5 0l3-3a4.6 4.6 0 0 0-6.5-6.5L11.5 6"/><path d="M14 10a4.6 4.6 0 0 0-6.5 0l-3 3A4.6 4.6 0 0 0 11 19.5L12.5 18"/></g></symbol>
  <symbol id="icon-arrow-up" viewBox="0 0 24 24"><path d="M12 19V5M6 11l6-6 6 6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></symbol>
  <symbol id="icon-arrow-down" viewBox="0 0 24 24"><path d="M12 5v14M6 13l6 6 6-6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></symbol>
  <symbol id="icon-menu" viewBox="0 0 24 24"><g stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></g></symbol>
  <symbol id="icon-send" viewBox="0 0 24 24"><path d="M4 12 20 4l-4 16-4.5-6L4 12z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></symbol>
</svg>`;
  function inject() {
    if (document.getElementById('icon-sprite')) return;
    const div = document.createElement('div');
    div.innerHTML = SPRITE;
    document.body.insertBefore(div.firstElementChild, document.body.firstChild);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
})();
