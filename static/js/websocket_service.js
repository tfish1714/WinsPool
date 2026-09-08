/**
 * WinsPool WebSocket Service Module
 * Handles real-time draft updates and score syncs.
 */

export class WebSocketService {
    constructor(callbacks) {
        this.socket = null;
        // { onMessage, onOpen, onClose, onError, onReconnectFailed }
        // onReconnectFailed fires once, only after maxReconnectAttempts is
        // truly exhausted -- lets the UI stop claiming "Reconnecting..."
        // once that's no longer true (see main.js's onClose banner).
        this.callbacks = callbacks;
        this.baseReconnectInterval = 3000;
        this.maxReconnectInterval = 30000; // exponential backoff, capped at 30s
        // A live draft room can be open for hours; a 15s-then-give-up cap
        // (the old maxReconnectAttempts=5 @ fixed 3s) meant any network blip
        // longer than that -- laptop sleep, WiFi switch, brief signal loss --
        // silently killed all live updates with zero user-visible warning
        // until a manual refresh. 200 attempts at up to 30s apart spans
        // ~90+ minutes before finally giving up.
        this.maxReconnectAttempts = 200;
        this.reconnectCount = 0;
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        console.log(`[WS] Connecting to ${wsUrl}...`);
        this.socket = new WebSocket(wsUrl);

        this.socket.onopen = () => {
            console.log('[WS] Connected');
            this.reconnectCount = 0;
            if (this.callbacks.onOpen) this.callbacks.onOpen();
        };

        this.socket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (this.callbacks.onMessage) this.callbacks.onMessage(data);
        };

        this.socket.onclose = () => {
            console.warn('[WS] Closed');
            if (this.callbacks.onClose) this.callbacks.onClose();
            this.attemptReconnect();
        };

        this.socket.onerror = (err) => {
            console.error('[WS] Error:', err);
            if (this.callbacks.onError) this.callbacks.onError(err);
        };
    }

    attemptReconnect() {
        if (this.reconnectCount < this.maxReconnectAttempts) {
            this.reconnectCount++;
            const delay = Math.min(
                this.baseReconnectInterval * Math.pow(1.5, this.reconnectCount - 1),
                this.maxReconnectInterval
            );
            console.log(`[WS] Reconnecting attempt ${this.reconnectCount} in ${Math.round(delay / 1000)}s...`);
            setTimeout(() => this.connect(), delay);
        } else {
            console.error('[WS] Max reconnect attempts reached');
            if (this.callbacks.onReconnectFailed) this.callbacks.onReconnectFailed();
        }
    }

    send(data) {
        if (this.socket && this.socket.readyState === WebSocket.OPEN) {
            this.socket.send(JSON.stringify(data));
        } else {
            console.error('[WS] Cannot send: Socket not open');
        }
    }
}
