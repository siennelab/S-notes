import { test } from 'node:test';
import assert from 'node:assert/strict';
import { connectQt } from '../web/markdown-editor/src/qt-bridge.js';

const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const tick = () => new Promise(resolve => setImmediate(resolve));

async function fixture(prepareClose = async () => {}) {
  const signals = {}, ready = [], failed = [], messages = [];
  let acknowledge = true;
  const host = Object.fromEntries(
    ['setMarkdown', 'setTheme', 'setReadOnly', 'prepareClose', 'cancelClose']
      .map(name => [name, { connect(fn) { signals[name] = fn; } }]),
  );
  for (const method of ['loadComplete', 'loadFailed', 'markdownChanged']) {
    host[method] = (...args) => {
      const ack = args.pop();
      messages.push({ method, args, ack });
      if (acknowledge) ack();
    };
  }
  Object.assign(host, { closeReady: token => ready.push(token), closeFailed: token => failed.push(token) });
  globalThis.document = { createElement: () => ({}), head: { append(script) { script.onload(); } } };
  globalThis.qt = { webChannelTransport: {} };
  globalThis.QWebChannel = class { constructor(_, done) { done({ objects: { host } }); } };
  const handlers = { prepareClose, cancelClose() {} };
  const bridge = await connectQt(handlers);
  return { bridge, signals, ready, failed, messages, handlers, ack(value) { acknowledge = value; } };
}

test('missing delivery ack is bounded by cancellation and re-delivered on retry', async () => {
  const f = await fixture();
  f.ack(false);
  f.bridge.changed('FICTIONAL last input', 'note-a');
  const first = f.signals.prepareClose('1');
  await tick();
  assert.deepEqual(f.ready, []);
  f.signals.cancelClose('1');
  await first;
  f.ack(true);
  await f.signals.prepareClose('2');
  assert.deepEqual(f.ready, ['2']);
  assert.equal(f.messages.length, 2);
  assert.deepEqual(f.messages[1].args, ['FICTIONAL last input', 'note-a']);
  f.messages[0].ack(); // A late original ack is harmless.
  assert.deepEqual(f.ready, ['2']);
});

test('retry cannot replay an old body after a newer body was acknowledged', async () => {
  const f = await fixture();
  f.ack(false);
  f.bridge.changed('old', 'note-a');
  f.ack(true);
  f.bridge.changed('newest', 'note-a');
  await f.signals.prepareClose('1');
  f.signals.cancelClose('1');
  await f.signals.prepareClose('2');
  assert.deepEqual(f.messages.map(message => message.args[0]), ['old', 'newest']);
  assert.deepEqual(f.ready, ['1', '2']);
});

test('missing load ack is retried before awaiting the load queue', async () => {
  const f = await fixture();
  f.ack(false);
  const queue = f.bridge.loaded('load-a', 'body');
  f.handlers.prepareClose = () => queue;
  const first = f.signals.prepareClose('1');
  await tick();
  f.signals.cancelClose('1');
  await first;
  f.ack(true);
  await f.signals.prepareClose('2');
  assert.deepEqual(f.ready, ['2']);
  assert.equal(f.messages.length, 2);
});

test('late preparation and old cancellation cannot finish or cancel a newer barrier', async () => {
  const old = deferred(), current = deferred();
  const f = await fixture(() => old.promise);
  const first = f.signals.prepareClose('1');
  f.signals.cancelClose('1');
  await first;
  f.handlers.prepareClose = () => current.promise;
  const second = f.signals.prepareClose('2');
  old.resolve();
  f.signals.cancelClose('1');
  await tick();
  assert.deepEqual(f.ready, []);
  current.resolve();
  await second;
  assert.deepEqual(f.ready, ['2']);
});

test('preparation rejection reports failure and the next barrier can succeed', async () => {
  const f = await fixture(async () => { throw new Error('synthetic failure'); });
  await f.signals.prepareClose('1');
  assert.deepEqual(f.failed, ['1']);
  assert.deepEqual(f.ready, []);
  f.signals.cancelClose('1');
  f.handlers.prepareClose = async () => {};
  await f.signals.prepareClose('2');
  assert.deepEqual(f.ready, ['2']);
});
