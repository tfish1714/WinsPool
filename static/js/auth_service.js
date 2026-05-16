/**
 * WinsPool Auth Service Module
 * Handles user sessions, login, and registration.
 */

export const AuthService = {
    getCredentials() {
        const pid = localStorage.getItem('nfl_wins_my_player_id');
        if (!pid || pid === 'null' || pid === 'undefined') {
            return { playerId: null, playerName: null, nickName: null, role: 'user', token: null };
        }
        return {
            playerId: pid,
            playerName: localStorage.getItem('nfl_wins_playerName'),
            nickName: localStorage.getItem('nfl_wins_nickName'),
            role: localStorage.getItem('nfl_wins_role') || 'user',
            token: localStorage.getItem('nfl_wins_token') || null,
        };
    },

    getToken() {
        return localStorage.getItem('nfl_wins_token') || null;
    },

    setCredentials(data) {
        localStorage.setItem('nfl_wins_my_player_id', data.playerId);
        localStorage.setItem('nfl_wins_playerName', data.playerName);
        localStorage.setItem('nfl_wins_nickName', data.nickName || '');
        localStorage.setItem('nfl_wins_user_email', data.email);
        localStorage.setItem('nfl_wins_role', data.role || 'user');
        if (data.token) {
            localStorage.setItem('nfl_wins_token', data.token);
        }
    },

    clearCredentials() {
        localStorage.clear();
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

    async syncProfile(playerId) {
        if (!playerId) return null;
        try {
            const resp = await fetch(`/api/profile?playerId=${playerId}`);
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
