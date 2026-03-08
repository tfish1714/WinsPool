/**
 * WinsPool WebSocket Service Module
 * Handles real-time draft updates and score syncs.
 */

export class WebSocketService {
    constructor(callbacks) {
        this.socket = null;
        this.callbacks = callbacks; // { onMessage, onOpen, onClose, onError }
        this.reconnectInterval = 3000;
        this.maxReconnectAttempts = 5;
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
            console.log(`[WS] Reconnecting attempt ${this.reconnectCount}...`);
            setTimeout(() => this.connect(), this.reconnectInterval);
        } else {
            console.error('[WS] Max reconnect attempts reached');
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
