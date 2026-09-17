/**
 * WebSocket client for the Evanovar RAM bridge.
 *
 * The application answers every request with exactly one JSON message, in the
 * order the requests were sent, so responses are matched to requests with a
 * simple FIFO queue.
 *
 * Commands are sent as `AUTH <token> | <command>` once the extension has been
 * linked by a pairing code.
 */

export const DEFAULT_PORT = 7963;

/** Addresses the local server may be bound to, tried in order. */
export const DEFAULT_HOSTS = ["127.0.0.1", "localhost"];

const DEFAULT_REQUEST_TIMEOUT_MS = 20000;
const DEFAULT_CONNECT_TIMEOUT_MS = 6000;

export class BridgeError extends Error {
  constructor(message, code = "") {
    super(message);
    this.name = "BridgeError";
    this.code = code;
  }
}

export class BridgeClient {
  constructor() {
    this._socket = null;
    this._pending = [];
  }

  get connected() {
    return Boolean(this._socket) && this._socket.readyState === WebSocket.OPEN;
  }

  /**
   * Open a connection to the application, trying each candidate host.
   * Resolves once a socket is open, rejects when every host failed.
   */
  async connect(port = DEFAULT_PORT, options = {}) {
    if (this.connected) {
      return;
    }

    const hosts = options.hosts || DEFAULT_HOSTS;
    const timeoutMs = options.timeoutMs || DEFAULT_CONNECT_TIMEOUT_MS;
    let lastError = null;

    for (const host of hosts) {
      try {
        await this._connectHost(host, port, timeoutMs);
        return;
      } catch (error) {
        lastError = error;
      }
    }

    throw lastError || new BridgeError("Could not connect to Evanovar RAM.");
  }

  /** Send a command and resolve with the `result` field of the response. */
  request(command, options = {}) {
    if (!this.connected) {
      return Promise.reject(new BridgeError("Not connected to Evanovar RAM."));
    }

    const { token = "", timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS } = options;
    const payload = token ? `AUTH ${token} | ${command}` : command;

    return new Promise((resolve, reject) => {
      const entry = { resolve, reject, timer: null };
      entry.timer = setTimeout(() => {
        this._pending = this._pending.filter((item) => item !== entry);
        reject(new BridgeError("The application did not answer in time."));
      }, timeoutMs);

      this._pending.push(entry);

      try {
        this._socket.send(payload);
      } catch (error) {
        clearTimeout(entry.timer);
        this._pending = this._pending.filter((item) => item !== entry);
        reject(new BridgeError(`Could not send the command: ${error.message}`));
      }
    });
  }

  /** Close the socket and reject anything still waiting for a response. */
  close(reason = "") {
    const socket = this._socket;
    this._socket = null;
    this._failAll(reason || "The connection to the application closed.");

    if (socket) {
      try {
        socket.close();
      } catch (error) {
        // The socket was already gone; nothing to clean up.
      }
    }
  }

  _connectHost(host, port, timeoutMs) {
    return new Promise((resolve, reject) => {
      this.close();

      let socket;
      try {
        socket = new WebSocket(`ws://${host}:${port}`);
      } catch (error) {
        reject(new BridgeError(`Could not open ws://${host}:${port}: ${error.message}`));
        return;
      }

      let settled = false;
      const finish = (callback, value) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timer);
        callback(value);
      };

      const timer = setTimeout(() => {
        finish(reject, new BridgeError(`Timed out connecting to ${host}:${port}.`));
        this.close();
      }, timeoutMs);

      socket.addEventListener("open", () => finish(resolve));
      socket.addEventListener("close", () => {
        finish(reject, new BridgeError(`Could not connect to ${host}:${port}.`));
      });
      socket.addEventListener("error", () => {
        finish(reject, new BridgeError(`Could not connect to ${host}:${port}.`));
      });
      socket.addEventListener("message", (event) => this._onMessage(event.data));

      this._socket = socket;
    });
  }

  _onMessage(data) {
    let payload;
    try {
      payload = JSON.parse(String(data));
    } catch (error) {
      console.warn("[Evanovar RAM] Ignoring a non-JSON message.", data);
      return;
    }

    const entry = this._pending.shift();
    if (!entry) {
      console.warn("[Evanovar RAM] Received an unexpected message.", payload);
      return;
    }

    clearTimeout(entry.timer);

    if (payload && payload.ok) {
      entry.resolve(payload.result ?? null);
    } else {
      entry.reject(
        new BridgeError(
          (payload && payload.error) || "The application reported an error.",
          (payload && payload.code) || ""
        )
      );
    }
  }

  _failAll(reason) {
    const pending = this._pending;
    this._pending = [];
    for (const entry of pending) {
      clearTimeout(entry.timer);
      entry.reject(new BridgeError(reason));
    }
  }
}
