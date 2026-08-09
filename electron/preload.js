'use strict';

// Intentionally minimal. CoreClip's frontend talks to the backend over plain
// HTTP/WebSocket on localhost, so it needs no privileged bridge. Keeping
// contextIsolation on with no exposed APIs keeps the renderer sandboxed.
//
// If the renderer ever needs native capabilities (file dialogs, OS
// notifications, etc.), expose them here via contextBridge.exposeInMainWorld.
