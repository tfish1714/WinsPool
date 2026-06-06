/**
 * chat.js — Draft room chat panel.
 * Exported functions are called from main.js handleWsMessage and onWsOpen.
 */

let _ws = null;
let _unreadCount = 0;
let _collapsed = false;
let _initialized = false;

export function initChat(ws) {
    _ws = ws;
    if (!_initialized) {
        _wireButtons();
        _initialized = true;
    }
}

export function loadHistory(messages) {
    const box = document.getElementById('chat-messages');
    if (!box) return;
    box.innerHTML = '';
    messages.forEach(m => _appendMessage(m.msgType ?? m.type, m.playerName, m.text, m.timestamp, false));
    _scrollBottom();
}

export function appendMessage(msgType, playerName, text, timestamp) {
    _appendMessage(msgType, playerName, text, timestamp, true);
}

function _appendMessage(msgType, playerName, text, timestamp, animate) {
    const box = document.getElementById('chat-messages');
    if (!box) return;

    const isSystem = msgType === 'system';
    const ts = _relTime(timestamp);
    const el = document.createElement('div');
    el.style.cssText = `font-size:0.8rem; padding:3px 6px; border-radius:5px; word-break:break-word;
        ${isSystem
            ? 'color:var(--text-secondary); font-style:italic; background:rgba(255,255,255,0.03);'
            : 'background:rgba(255,255,255,0.05);'}`;
    el.innerHTML = isSystem
        ? `<span>${_esc(text)}</span> <span style="color:var(--text-secondary);font-size:0.7rem;">${ts}</span>`
        : `<span style="font-weight:600; color:var(--accent-gold);">${_esc(playerName)}</span>
           <span style="color:var(--text-secondary);"> ${ts}</span><br>${_esc(text)}`;
    box.appendChild(el);

    const atBottom = box.scrollHeight - box.clientHeight - box.scrollTop < 60;
    if (atBottom || !animate) {
        _scrollBottom();
    } else if (animate && _collapsed) {
        _bumpUnread();
    }
}

function _wireButtons() {
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('chat-send-btn');
    const teamsBtn = document.getElementById('chat-teams-btn');
    const collapseBtn = document.getElementById('chat-collapse-btn');
    const header = document.getElementById('chat-header');

    sendBtn?.addEventListener('click', _sendMessage);
    input?.addEventListener('keydown', e => { if (e.key === 'Enter') _sendMessage(); });

    teamsBtn?.addEventListener('click', () => {
        _ws?.send({ action: 'teams_list' });
    });

    const toggle = () => {
        const body = document.getElementById('chat-body');
        if (!body) return;
        _collapsed = !_collapsed;
        body.style.display = _collapsed ? 'none' : 'flex';
        if (collapseBtn) collapseBtn.textContent = _collapsed ? '+' : '−';
        if (!_collapsed) _clearUnread();
    };

    collapseBtn?.addEventListener('click', e => { e.stopPropagation(); toggle(); });
    header?.addEventListener('click', toggle);
}

function _sendMessage() {
    const input = document.getElementById('chat-input');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    _ws?.send({ action: 'chat', text });
    input.value = '';
}

function _scrollBottom() {
    const box = document.getElementById('chat-messages');
    if (box) box.scrollTop = box.scrollHeight;
}

function _bumpUnread() {
    _unreadCount++;
    const badge = document.getElementById('chat-unread-badge');
    if (badge) { badge.textContent = _unreadCount; badge.style.display = 'inline'; }
}

function _clearUnread() {
    _unreadCount = 0;
    const badge = document.getElementById('chat-unread-badge');
    if (badge) badge.style.display = 'none';
}

function _relTime(iso) {
    if (!iso) return '';
    const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    return `${Math.floor(diff / 3600)}h ago`;
}

function _esc(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
