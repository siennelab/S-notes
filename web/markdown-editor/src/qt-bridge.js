// The complete desktop boundary: four incoming signals, five outgoing slots.
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
  const pending = new Set();
  function send(method, ...args) {
    const delivery = new Promise(resolve => host[method](...args, resolve));
    pending.add(delivery);
    delivery.finally(() => pending.delete(delivery));
    return delivery;
  }
  host.setMarkdown.connect(handlers.setMarkdown);
  host.setTheme.connect(handlers.setTheme);
  host.setReadOnly.connect(handlers.setReadOnly);
  host.prepareClose.connect(async () => {
    await handlers.prepareClose();
    await Promise.all([...pending]);
    host.closeReady();
  });
  return {
    ready: () => host.editorReady(),
    loaded: (id, markdown) => send("loadComplete", id, markdown),
    failed: (id, reason) => send("loadFailed", id, reason),
    changed: (markdown, id) => send("markdownChanged", markdown, id),
  };
}
