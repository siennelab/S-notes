// The complete desktop boundary, including cancellable close barriers.
export async function connectQt(handlers) {
  await new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "qrc:///qtwebchannel/qwebchannel.js";
    script.onload = resolve;
    script.onerror = () => reject(new Error("QWebChannel unavailable"));
    document.head.append(script);
  });
  const channel = await new Promise(resolve => new QWebChannel(qt.webChannelTransport, resolve));
  const host = channel.objects.host;
  const pending = new Map();
  let activeClose = null;
  let retryPending = false;
  function send(method, ...args) {
    const id = method === 'markdownChanged' ? args[1] : args[0];
    const key = `${method}:${id}`;
    const entry = {};
    entry.delivery = new Promise(resolve => {
      entry.deliver = () => host[method](...args, () => {
        if (pending.get(key) === entry) pending.delete(key);
        resolve();
      });
    });
    // Qt slots are synchronous and channel messages are ordered. The newest
    // body/ack covers earlier bodies for this load; never replay an older body
    // after a newer one has already been acknowledged.
    pending.set(key, entry);
    entry.deliver();
    return entry.delivery;
  }
  host.setMarkdown.connect(handlers.setMarkdown);
  host.setTheme.connect(handlers.setTheme);
  host.setReadOnly.connect(handlers.setReadOnly);
  host.cancelClose.connect(token => {
    if (activeClose?.token !== token) return;
    activeClose.cancel();
    activeClose = null;
    retryPending = true;
    handlers.cancelClose();
  });
  host.prepareClose.connect(async token => {
    const attempt = { token };
    const cancelled = new Promise(resolve => { attempt.cancel = resolve; });
    activeClose = attempt;
    const isCurrent = () => activeClose === attempt;
    try {
      // Also unblock a queue awaiting loadComplete's missing acknowledgement.
      // Retry the original message, never pull a possibly stale Python body.
      if (retryPending) {
        retryPending = false;
        for (const entry of pending.values()) entry.deliver();
      }
      await Promise.race([handlers.prepareClose(isCurrent, cancelled), cancelled]);
      if (!isCurrent()) return;
      await Promise.race([Promise.all([...pending.values()].map(entry => entry.delivery)), cancelled]);
      if (isCurrent()) host.closeReady(token);
    } catch {
      if (isCurrent()) host.closeFailed(token);
    }
  });
  return {
    ready: () => host.editorReady(),
    loaded: (id, markdown) => send("loadComplete", id, markdown),
    failed: (id, reason) => send("loadFailed", id, reason),
    changed: (markdown, id) => send("markdownChanged", markdown, id),
  };
}
