import { Editor, rootCtx, defaultValueCtx, editorViewCtx, serializerCtx } from "@milkdown/kit/core";
import * as cm from "@milkdown/kit/preset/commonmark";
import { gfm, toggleStrikethroughCommand } from "@milkdown/kit/preset/gfm";
import { history, undoCommand, redoCommand } from "@milkdown/kit/plugin/history";
import { listItemBlockComponent } from "@milkdown/kit/component/list-item-block";
import { clipboard } from "@milkdown/kit/plugin/clipboard";
import { callCommand, getMarkdown, $prose } from "@milkdown/kit/utils";
import { Plugin, TextSelection } from "@milkdown/kit/prose/state";
import "@milkdown/kit/prose/view/style/prosemirror.css";
import "./style.css";
import { bodySizeExtension, setBodySize, setHeading } from "./body-size.js";
import { listStyleExtension, toggleList } from "./list-style.js";
import { connectQt } from "./qt-bridge.js";

const $ = id => document.getElementById(id);
let editor = null, activeId = null, requestedId = null, bridge;
let loading = true, readOnly = false, closing = false, queue = Promise.resolve();
const current = () => editor.action(getMarkdown());
const writable = () => editor && !loading && !readOnly && !closing;
function controls() {
  if (editor) editor.action(ctx => ctx.get(editorViewCtx).setProps({ editable: () => Boolean(writable()) }));
  document.querySelectorAll('#toolbar button, #replace, #replace-all, #replacement, #link-url').forEach(el => { el.disabled = !writable(); });
  $("find-next").disabled = !editor || loading;
}
function updated(markdown) { $("raw").value = markdown; }
function changed(markdown, id) {
  if (loading || id !== activeId || id !== requestedId) return;
  updated(markdown);
  bridge.changed(markdown, id);
}
// Blur ends native composition; allow its final DOM mutation/transaction to flush.
// The normal transaction observer sends Markdown. Closing never pulls a body.
async function finishComposition() {
  if (!editor) return;
  const view = editor.action(ctx => ctx.get(editorViewCtx));
  view.dom.blur();
  await new Promise(resolve => setTimeout(resolve, 30));
  view.domObserver.flush();
}
async function mount(markdown, id) {
  if (id !== requestedId || closing) return;
  await finishComposition();
  const root = document.createElement('div');
  root.hidden = true;
  $("editor").append(root);
  let candidate;
  try {
    const changes = $prose(ctx => new Plugin({
      filterTransaction: tr => !tr.docChanged || (id === activeId && Boolean(writable())),
      view: () => ({ update(view, previous) {
        if (!view.state.doc.eq(previous.doc)) changed(ctx.get(serializerCtx)(view.state.doc), id);
      }}),
    }));
    candidate = Editor.make()
      .config(ctx => { ctx.set(rootCtx, root); ctx.set(defaultValueCtx, markdown); })
      .use(cm.commonmark).use(gfm).use(history).use(clipboard)
      .use(listItemBlockComponent).use(bodySizeExtension).use(listStyleExtension).use(changes);
    await candidate.create();
    const canonical = candidate.action(getMarkdown()); // Explicit even for empty/identical documents.
    if (id !== requestedId || closing) { await candidate.destroy(); root.remove(); return; }
    const previous = editor;
    editor = candidate;
    activeId = id;
    if (previous) await previous.destroy();
    for (const child of [...$("editor").children]) if (child !== root) child.remove();
    root.hidden = false;
    // Keep editing locked until Python has accepted the canonical baseline.
    updated(canonical);
    await bridge.loaded(id, canonical);
    if (id !== requestedId || closing) return;
    loading = false;
    $("status").textContent = "已加载 · 正文 17px";
    controls();
  } catch (error) {
    // Milkdown stays OnCreate after a rejected create(); destroy() would wait
    // forever in that state. Dispose a partial view without awaiting that loop.
    if (candidate && candidate !== editor) {
      try {
        if (candidate.status === 'Created') await candidate.destroy();
        else candidate.action(ctx => ctx.get(editorViewCtx))?.destroy();
      } catch {}
    }
    root.remove();
    if (id !== requestedId) return;
    loading = true;
    controls();
    $("status").textContent = `加载失败，禁止保存：${error.message}`;
    await bridge.failed(id, String(error.message || error));
  }
}
const commands = [
  ["H1", setHeading, 1], ["H2", setHeading, 2], ["H3", setHeading, 3],
  ["15", setBodySize, 15], ["17", setBodySize, 17], ["20", setBodySize, 20],
  ["Bold", cm.toggleStrongCommand], ["Italic", cm.toggleEmphasisCommand], ["Strike", toggleStrikethroughCommand],
  ["Bullet list", toggleList, "bullet_list"], ["Ordered list", toggleList, "ordered_list"],
  ["Quote", cm.wrapInBlockquoteCommand], ["Inline code", cm.toggleInlineCodeCommand], ["Code block", cm.createCodeBlockCommand],
  ["Link", cm.toggleLinkCommand], ["Undo", undoCommand], ["Redo", redoCommand],
];
for (const [label, command, payload] of commands) {
  const button = document.createElement('button');
  button.textContent = label;
  button.onmousedown = event => event.preventDefault();
  button.onclick = () => {
    if (!writable()) return;
    let value = payload;
    if (label === 'Link') {
      const href = $('link-url').value.trim();
      if (!/^(https?:\/\/|mailto:)/i.test(href)) { $('status').textContent = '请输入 http、https 或 mailto 链接'; return; }
      value = { href };
    }
    if (label !== 'Link' || !editor.action(callCommand(cm.updateLinkCommand.key, value))) {
      const applied = editor.action(callCommand(command.key, value));
      if (command === setBodySize && !applied) $('status').textContent = '请选中正文文字，或将光标放在标题内，再选择字号。';
    }
    editor.action(ctx => ctx.get(editorViewCtx).focus());
  };
  $('toolbar').append(button);
}

// Search textblocks with ProseMirror positions; inline formatting does not split matches.
function matches() {
  const needle = $('find').value;
  if (!editor || !needle) return [];
  const found = [];
  editor.action(ctx => ctx.get(editorViewCtx).state.doc.descendants((node, pos) => {
    if (!node.isTextblock) return;
    const text = node.textBetween(0, node.content.size, '', '\ufffc');
    for (let i = text.indexOf(needle); i !== -1; i = text.indexOf(needle, i + needle.length))
      found.push({ from: pos + 1 + i, to: pos + 1 + i + needle.length });
    return false;
  }));
  $('find-status').textContent = `${found.length} 处匹配`;
  return found;
}
$('find-next').onclick = () => {
  if (loading) return;
  const found = matches();
  if (!found.length) return;
  editor.action(ctx => {
    const view = ctx.get(editorViewCtx);
    const hit = found.find(item => item.from >= view.state.selection.to) || found[0];
    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, hit.from, hit.to)).scrollIntoView());
    view.focus();
  });
};
$('find').oninput = matches;
$('replace').onmousedown = event => event.preventDefault();
$('replace').onclick = () => {
  if (!writable()) return;
  const found = matches();
  editor.action(ctx => {
    const view = ctx.get(editorViewCtx), { from, to } = view.state.selection;
    if (found.some(hit => hit.from === from && hit.to === to)) view.dispatch(view.state.tr.insertText($('replacement').value, from, to));
  });
  $('find-next').click();
};
$('replace-all').onclick = () => {
  if (!writable()) return;
  const found = matches();
  editor.action(ctx => {
    const view = ctx.get(editorViewCtx), tr = view.state.tr;
    for (const hit of found.reverse()) tr.insertText($('replacement').value, hit.from, hit.to);
    if (tr.docChanged) view.dispatch(tr);
  });
  matches();
};
document.addEventListener('keydown', event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'f') { event.preventDefault(); $('find').focus(); }
});
// Milkdown suppresses ordinary anchor navigation while editing. Explicitly
// request a new window so Qt can route the link to the system browser.
document.addEventListener('click', event => {
  const link = event.target.closest?.('#editor a[href]');
  if (!link) return;
  event.preventDefault();
  event.stopPropagation();
  if (/^(https?:\/\/|mailto:)/i.test(link.href)) window.open(link.href, '_blank');
}, true);
// Qt owns external navigation. File drops must never navigate or import files.
document.addEventListener('dragover', event => event.preventDefault());
document.addEventListener('drop', event => event.preventDefault(), true);

controls();
try {
  bridge = await connectQt({
    setMarkdown(markdown, id) {
      if (closing) return;
      requestedId = id;
      loading = true;
      controls();
      $('status').textContent = '正在加载 · 暂不可保存';
      queue = queue.then(() => mount(markdown, id));
    },
    setTheme(theme) {
      for (const [key, value] of Object.entries(theme)) if (/^#[0-9a-f]{6}$/i.test(value)) document.documentElement.style.setProperty(`--${key.replaceAll('_', '-')}`, value);
      document.documentElement.style.colorScheme = theme.name === 'dark' ? 'dark' : 'light';
    },
    setReadOnly(value) { readOnly = value; controls(); },
    async prepareClose() {
      await queue;
      await finishComposition();
      closing = true;
      controls();
    },
  });
  bridge.ready();
} catch (error) { $('status').textContent = `启动失败：${error.message}`; }
