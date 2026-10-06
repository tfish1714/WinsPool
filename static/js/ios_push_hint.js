// iOS Safari only supports Web Push for sites added to the Home Screen. In a
// plain Safari tab 'PushManager' is missing, so push setup used to exit
// silently. This module shows a dismissible hint explaining how to install.
// shouldShowIosInstallHint is pure (node-testable); DOM/storage use is guarded.

export const IOS_HINT_TEXT =
    "To enable push notifications on iOS, tap the Share button and select 'Add to Home Screen'";
export const IOS_HINT_DISMISSED_KEY = 'nfl_wins_ios_push_hint_dismissed';

export function isIosDevice({ userAgent = '', platform = '', maxTouchPoints = 0 } = {}) {
    if (/iPhone|iPad|iPod/.test(userAgent)) return true;
    // iPadOS 13+ reports a desktop Mac UA; touch support distinguishes it.
    return platform === 'MacIntel' && maxTouchPoints > 1;
}

export function shouldShowIosInstallHint({
    userAgent = '', platform = '', maxTouchPoints = 0, standalone = false, dismissed = false,
} = {}) {
    if (standalone || dismissed) return false;
    return isIosDevice({ userAgent, platform, maxTouchPoints });
}

function _readDismissed() {
    try {
        return localStorage.getItem(IOS_HINT_DISMISSED_KEY) === '1';
    } catch (e) {
        return false;
    }
}

function _writeDismissed() {
    try {
        localStorage.setItem(IOS_HINT_DISMISSED_KEY, '1');
    } catch (e) { /* storage blocked: banner just returns next page load */ }
}

export function renderIosInstallBanner() {
    if (typeof document === 'undefined') return null;
    const existing = document.querySelector('.ios-install-banner');
    if (existing) return existing;
    const banner = document.createElement('div');
    banner.className = 'ios-install-banner';
    banner.setAttribute('role', 'status');
    const text = document.createElement('span');
    text.className = 'ios-install-banner__text';
    text.textContent = IOS_HINT_TEXT;
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'ios-install-banner__dismiss';
    close.setAttribute('aria-label', 'Dismiss install hint');
    close.textContent = '×';
    close.addEventListener('click', () => {
        _writeDismissed();
        banner.remove();
    });
    banner.append(text, close);
    document.body.appendChild(banner);
    return banner;
}

// Once per page load; safe to call from several places.
let _checked = false;
export function maybeShowIosInstallHint() {
    if (_checked || typeof window === 'undefined' || typeof document === 'undefined') return false;
    _checked = true;
    const nav = window.navigator || {};
    const show = shouldShowIosInstallHint({
        userAgent: nav.userAgent || '',
        platform: nav.platform || '',
        maxTouchPoints: nav.maxTouchPoints || 0,
        standalone: nav.standalone === true,
        dismissed: _readDismissed(),
    });
    if (show) renderIosInstallBanner();
    return show;
}
