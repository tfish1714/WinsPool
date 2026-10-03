/**
 * WinsPool Auth Service Module
 * Handles user sessions, login, and registration.
 */

export const STORAGE_KEYS = {
    TOKEN: 'nfl_wins_token',
    PLAYER_ID: 'nfl_wins_my_player_id',
    ROLE: 'nfl_wins_role',
    DRAFT_ACTIVE: 'nfl_wins_draft_active',
    THEME: 'nfl_wins_theme',
};

/**
 * Bearer header map for the saved session token, or {} when there is no token
 * (or storage is unavailable). Safe to spread into any fetch headers object.
 */
export function getAuthHeaders() {
    let token = null;
    try {
        token = localStorage.getItem(STORAGE_KEYS.TOKEN);
    } catch (e) { /* storage unavailable; cookie auth still applies */ }
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

export const AuthService = {
    getAuthHeaders,

    getCredentials() {
        const pid = localStorage.getItem(STORAGE_KEYS.PLAYER_ID);
        if (!pid || pid === 'null' || pid === 'undefined') {
            return { playerId: null, playerName: null, nickName: null, role: 'user', token: null };
        }
        return {
            playerId: pid,
            playerName: localStorage.getItem('nfl_wins_playerName'),
            nickName: localStorage.getItem('nfl_wins_nickName'),
            role: localStorage.getItem(STORAGE_KEYS.ROLE) || 'user',
            token: localStorage.getItem(STORAGE_KEYS.TOKEN) || null,
        };
    },

    getToken() {
        return localStorage.getItem(STORAGE_KEYS.TOKEN) || null;
    },

    setCredentials(data) {
        localStorage.setItem(STORAGE_KEYS.PLAYER_ID, data.playerId);
        localStorage.setItem('nfl_wins_playerName', data.playerName);
        localStorage.setItem('nfl_wins_nickName', data.nickName || '');
        localStorage.setItem('nfl_wins_user_email', data.email);
        localStorage.setItem(STORAGE_KEYS.ROLE, data.role || 'user');
        if (data.token) {
            localStorage.setItem(STORAGE_KEYS.TOKEN, data.token);
        }
    },

    clearCredentials() {
        // The theme is a device preference, not a credential: keep it across sign-out.
        let theme = null;
        try { theme = localStorage.getItem(STORAGE_KEYS.THEME); } catch (e) { /* storage unavailable */ }
        localStorage.clear();
        if (theme) {
            try { localStorage.setItem(STORAGE_KEYS.THEME, theme); } catch (e) { /* storage unavailable */ }
        }
    },

    async checkAccount(email) {
        const resp = await fetch(`/api/check_player?email=${encodeURIComponent(email)}`);
        return await resp.json();
    },

    async login(email, password) {
        const resp = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
        return await resp.json();
    },

    async verifyMfa(playerId, code) {
        const resp = await fetch('/api/mfa/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ playerId, code })
        });
        return await resp.json();
    },

    async syncProfile() {
        if (!this.getToken()) return null;
        try {
            const resp = await fetch('/api/profile', {
                headers: getAuthHeaders()
            });
            if (resp.ok) {
                const data = await resp.json();
                this.setCredentials({
                    playerId: data.playerId,
                    playerName: data.fullName,
                    nickName: data.nickName,
                    email: data.email,
                    role: data.role
                });
                return data;
            }
        } catch (e) {
            console.error('[Auth] Profile sync failed', e);
        }
        return null;
    }
};
