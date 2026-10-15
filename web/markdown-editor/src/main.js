import { Editor, rootCtx, editorViewCtx, editorStateCtx, parserCtx, serializerCtx, remarkStringifyOptionsCtx } from "@milkdown/kit/core";
import * as cm from "@milkdown/kit/preset/commonmark";
import { gfm, toggleStrikethroughCommand } from "@milkdown/kit/preset/gfm";
import { history, undoCommand, redoCommand } from "@milkdown/kit/plugin/history";
import { listItemBlockComponent } from "@milkdown/kit/component/list-item-block";
import { clipboard } from "@milkdown/kit/plugin/clipboard";
import { callCommand, getMarkdown, $prose } from "@milkdown/kit/utils";
import { EditorState, Plugin, TextSelection } from "@milkdown/kit/prose/state";
import "@milkdown/kit/prose/view/style/prosemirror.css";
import "./style.css";
import { bodySizeExtension, setBodySize, setHeading } from "./body-size.js";
import { toggleCodeBlock } from "./code-block.js";
import { listStyleExtension, toggleList } from "./list-style.js";
import { connectQt } from "./qt-bridge.js";

const $ = id => document.getElementById(id);
let editor = null, activeId = null, requestedId = null, bridge;
let loading = true, readOnly = false, closing = false, queue = Promise.resolve();
const writable = () => editor && !loading && !readOnly && !closing;
const editorView = () => editor.action(ctx => ctx.get(editorViewCtx));
function controls() {
  if (editor) editorView().setProps({ editable: () => Boolean(writable()) });
  document.querySelectorAll('#toolbar button, #replace, #replace-all, #replacement, #link-url').forEach(el => { el.disabled = !writable(); });
  const unavailable = !editor || loading;
  $("find-next").disabled = unavailable;
  for (const key of ['Raw Markdown', 'Search']) if (buttons.has(key)) buttons.get(key).disabled = unavailable;
  if (editor) updateToolbar(editorView().state);
}
function updated(markdown) { $("raw").value = markdown; }
function changed(markdown, id) {
  if (loading || id === null || id !== activeId || id !== requestedId) return;
  updated(markdown);
  bridge.changed(markdown, id);
}
// Blur ends native composition; allow its final DOM mutation/transaction to
// flush. Native input events and channel messages travel on different IPC
// paths, so the delivery barrier keeps a short grace period unless told the
// composition state alone is sufficient (a load that follows a barrier).
// The normal transaction observer sends Markdown. Closing never pulls a body.
async function finishComposition(onlyWhenComposing = false, isCurrent = () => true) {
  if (!editor) return;
  const current = editorView();
  const composing = current.composing;
  current.dom.blur();
  if (composing || !onlyWhenComposing) await new Promise(resolve => setTimeout(resolve, 30));
  if (!isCurrent()) return;
  current.domObserver.flush();
}
// One Milkdown/ProseMirror stack for the life of the page. Note switches replace
// the document inside the existing EditorView; nothing is remounted.
const changes = $prose(ctx => new Plugin({
  filterTransaction: tr => !tr.docChanged || Boolean(writable()),
  view: () => ({ update(current, previous) {
    updateToolbar(current.state);
    if (!current.state.doc.eq(previous.doc)) {
      matches();
      changed(ctx.get(serializerCtx)(current.state.doc), activeId);
    }
  }}),
}));
async function ensureEditor() {
  if (editor) return;
  const root = document.createElement('div');
  $("editor").append(root);
  let candidate;
  try {
    candidate = Editor.make()
      .config(ctx => {
        ctx.set(rootCtx, root);
        ctx.update(remarkStringifyOptionsCtx, options => ({
          ...options,
          handlers: {
            ...options.handlers,
            // The default text handler bypasses escaping for text ending in
            // whitespace. That can turn literal bullets into lists on reload.
            // Entities also retain whitespace-only paragraphs and indentation.
            text: (node, parent, state, info) => state.safe(node.value, { ...info, encode: [] })
              .replace(/^[ \t]+|[ \t]+$/gm, spaces => [...spaces]
                .map(char => `&#${char.charCodeAt(0)};`).join('')),
          },
        }));
      })
      .use(cm.commonmark).use(gfm).use(history).use(clipboard)
      .use(listItemBlockComponent).use(bodySizeExtension).use(toggleCodeBlock).use(listStyleExtension).use(changes);
    await candidate.create();
    editor = candidate;
  } catch (error) {
    // Milkdown stays OnCreate after a rejected create(); destroy() would wait
    // forever in that state. Dispose a partial view without awaiting that loop.
    try {
      if (candidate?.status === 'Created') await candidate.destroy();
      else candidate?.action(ctx => ctx.get(editorViewCtx))?.destroy();
    } catch {}
    root.remove();
    throw error;
  }
}
async function discardEditor() {
  const previous = editor;
  editor = null;
  try {
    if (previous?.status === 'Created') await Promise.race([previous.destroy(), new Promise(resolve => setTimeout(resolve, 1000))]);
    else previous?.action(ctx => ctx.get(editorViewCtx))?.destroy();
  } catch {}
  $("editor").replaceChildren();
}
// Parse with the editor's own parser, then swap the whole state so history,
// decorations and every plugin state restart for the new note.
function installDocument(markdown) {
  return editor.action(ctx => {
    const current = ctx.get(editorViewCtx);
    const doc = ctx.get(parserCtx)(markdown);
    const state = EditorState.create({ schema: current.state.schema, doc, plugins: current.state.plugins });
    current.updateState(state);
    ctx.set(editorStateCtx, state);
    document.querySelector('.panes > section').scrollTop = 0;
    return ctx.get(serializerCtx)(state.doc); // Explicit even for empty/identical documents.
  });
}
async function load(markdown, id) {
  if (id !== requestedId || closing) return;
  await finishComposition(true);
  if (id !== requestedId || closing) return;
  let canonical;
  try {
    await ensureEditor();
    if (id !== requestedId || closing) return;
    activeId = null;
    canonical = installDocument(markdown);
    activeId = id;
  } catch (error) {
    // Never show the previous body beneath the requested note. A half-applied
    // document is not trusted: discard the stack and rebuild it on the next load.
    activeId = null;
    await discardEditor();
    updated('');
    loading = true;
    controls();
    if (id !== requestedId) return;
    await bridge.failed(id, String(error.message || error));
    return;
  }
  // Keep editing locked until Python has accepted the canonical baseline.
  updated(canonical);
  await bridge.loaded(id, canonical);
  if (id !== requestedId || closing) return;
  loading = false;
  controls();
  matches();
}
// Toolbar state is presentation derived from the current selection only.
const icons = {
  bullet: '<circle cx="4" cy="5" r="1"/><circle cx="4" cy="10" r="1"/><circle cx="4" cy="15" r="1"/><path d="M8 5h9M8 10h9M8 15h9"/>',
  ordered: '<path d="M2 3h1v5M2 8h3M2 12c3-2 4 1 1 3l-1 2h3M8 5h9M8 10h9M8 15h9"/>',
  quote: '<path d="M3 6h5v5H4c0 3 2 4 3 4M12 6h5v5h-4c0 3 2 4 3 4"/>',
  inline: '<path d="m7 6-4 4 4 4m6-8 4 4-4 4"/>',
  code: '<rect x="2" y="3" width="16" height="14" rx="2"/><path d="m7 7-3 3 3 3m6-6 3 3-3 3"/>',
  link: '<path d="m8 6 2-2a4 4 0 0 1 6 6l-2 2m-2 2-2 2a4 4 0 0 1-6-6l2-2m1 5 6-6"/>',
  undo: '<path d="m7 4-4 4 4 4M3 8h8a5 5 0 0 1 0 10"/>',
  redo: '<path d="m13 4 4 4-4 4m4-4H9a5 5 0 0 0 0 10"/>',
  search: '<circle cx="8" cy="8" r="5"/><path d="m12 12 5 5"/>',
};
const buttons = new Map();
function button(key, label, tooltip, action, icon) {
  const el = document.createElement('button');
  el.type = 'button';
  el.dataset.command = key;
  el.title = tooltip;
  el.setAttribute('aria-label', tooltip);
  if (icon) el.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true">${icons[icon]}</svg>`;
  else el.textContent = label;
  el.onmousedown = event => event.preventDefault();
  el.onclick = action;
  $('toolbar').append(el);
  buttons.set(key, el);
  return el;
}
function pressed(key, active) { buttons.get(key)?.setAttribute('aria-pressed', String(Boolean(active))); }
function updateToolbar(state) {
  const { $from, from, to, empty } = state.selection;
  const nodes = [];
  for (let depth = 0; depth <= $from.depth; depth++) nodes.push($from.node(depth));
  const block = $from.parent;
  const marks = empty ? [state.storedMarks || $from.marks()] : [];
  if (!empty) state.doc.nodesBetween(from, to, node => { if (node.isText) marks.push(node.marks); });
  const hasMark = name => marks.length > 0 && marks.every(set => set.some(mark => mark.type.name === name));
  for (const level of [1, 2, 3]) pressed(`H${level}`, block.type.name === 'heading' && block.attrs.level === level);
  for (const size of [15, 17, 20, 24]) pressed(String(size), block.type.name === 'paragraph' && marks.length > 0
    && marks.every(set => (set.find(mark => mark.type.name === 'body_size')?.attrs.size || '20') === String(size)));
  for (const [key, name] of [['Bold','strong'], ['Italic','emphasis'], ['Strike','strike_through'], ['Inline code','inlineCode'], ['Link','link']]) pressed(key, hasMark(name));
  for (const [key, name] of [['Bullet list','bullet_list'], ['Ordered list','ordered_list'], ['Quote','blockquote'], ['Code block','code_block']]) pressed(key, nodes.some(node => node.type.name === name));
}
const commands = [
  ['H1', 'H1', '一级标题', setHeading, 1], ['H2', 'H2', '二级标题', setHeading, 2], ['H3', 'H3', '三级标题', setHeading, 3],
  ['15', '15', '正文 15px', setBodySize, 15], ['17', '17', '正文 17px', setBodySize, 17], ['20', '20', '正文 20px', setBodySize, 20],
  ['24', '24', '正文 24px', setBodySize, 24],
  ['Bold', 'B', '加粗', cm.toggleStrongCommand], ['Italic', 'I', '斜体', cm.toggleEmphasisCommand], ['Strike', 'S', '删除线', toggleStrikethroughCommand],
  ['Bullet list', '', '无序列表', toggleList, 'bullet_list', 'bullet'], ['Ordered list', '', '有序列表', toggleList, 'ordered_list', 'ordered'],
  ['Quote', '', '引用', cm.wrapInBlockquoteCommand, undefined, 'quote'], ['Inline code', '', '行内代码', cm.toggleInlineCodeCommand, undefined, 'inline'],
  ['Code block', '', '代码块', toggleCodeBlock, undefined, 'code'],
];
for (const [key, label, tooltip, command, payload, icon] of commands) {
  button(key, label, tooltip, () => {
    if (!writable()) return;
    editor.action(callCommand(command.key, payload));
    editor.action(ctx => { const view = ctx.get(editorViewCtx); view.focus(); updateToolbar(view.state); });
  }, icon);
}
let linkSelection = null;
function hideLink(focus = false) {
  $('link-field').hidden = true;
  $('link-url').setCustomValidity('');
  linkSelection = null;
  buttons.get('Link')?.setAttribute('aria-expanded', 'false');
  if (focus && editor) editor.action(ctx => ctx.get(editorViewCtx).focus());
}
button('Link', '', '链接', () => {
  if (!writable()) return;
  if (!$('link-field').hidden) { hideLink(true); return; }
  editor.action(ctx => {
    const view = ctx.get(editorViewCtx);
    linkSelection = { id: activeId, bookmark: view.state.selection.getBookmark() };
    const link = view.state.selection.$from.marks().find(mark => mark.type.name === 'link');
    $('link-url').value = link?.attrs.href || '';
  });
  $('link-field').hidden = false;
  buttons.get('Link').setAttribute('aria-expanded', 'true');
  $('link-url').focus();
  $('link-url').select();
}, 'link');
$('link-field').onsubmit = event => {
  event.preventDefault();
  if (!writable() || linkSelection?.id !== activeId) return;
  const href = $('link-url').value.trim();
  if (!/^(https?:\/\/|mailto:)/i.test(href)) {
    $('link-url').setCustomValidity('请输入 http、https 或 mailto 链接');
    $('link-url').reportValidity();
    return;
  }
  editor.action(ctx => {
    const view = ctx.get(editorViewCtx);
    view.dispatch(view.state.tr.setSelection(linkSelection.bookmark.resolve(view.state.doc)));
  });
  if (!editor.action(callCommand(cm.updateLinkCommand.key, { href }))) editor.action(callCommand(cm.toggleLinkCommand.key, { href }));
  hideLink(true);
};
$('link-url').oninput = () => $('link-url').setCustomValidity('');
for (const [key, tooltip, command, icon] of [['Undo', '撤销', undoCommand, 'undo'], ['Redo', '重做', redoCommand, 'redo']]) {
  button(key, '', tooltip, () => { if (writable()) { editor.action(callCommand(command.key)); editor.action(ctx => ctx.get(editorViewCtx).focus()); } }, icon);
}
button('Raw Markdown', 'MD', '查看 Markdown 源码（只读）', () => {
  $('raw-pane').hidden = !$('raw-pane').hidden;
  document.querySelector('.panes').classList.toggle('with-raw', !$('raw-pane').hidden);
  pressed('Raw Markdown', !$('raw-pane').hidden);
  buttons.get('Raw Markdown').setAttribute('aria-expanded', String(!$('raw-pane').hidden));
});
function showSearch(show) {
  $('find-bar').hidden = !show;
  pressed('Search', show);
  buttons.get('Search').setAttribute('aria-expanded', String(show));
  if (show) $('find').focus();
  else if (editor) editor.action(ctx => ctx.get(editorViewCtx).focus());
}
button('Search', '', '查找与替换（⌘F）', () => showSearch($('find-bar').hidden), 'search');
for (const [key, target] of [['Link', 'link-field'], ['Raw Markdown', 'raw-pane'], ['Search', 'find-bar']]) {
  buttons.get(key).setAttribute('aria-controls', target);
  buttons.get(key).setAttribute('aria-expanded', 'false');
}
pressed('Raw Markdown', false);
pressed('Search', false);

// Search textblocks with ProseMirror positions; inline formatting does not split matches.
const foldSearchText = value => value.replace(/[A-Z]/g, char => char.toLowerCase());
function matches() {
  const needle = $('find').value;
  if (!editor || loading || !needle) {
    $('find-status').textContent = '';
    return [];
  }
  const foldedNeedle = foldSearchText(needle);
  const found = [];
  editor.action(ctx => ctx.get(editorViewCtx).state.doc.descendants((node, pos) => {
    if (!node.isTextblock) return;
    const text = node.textBetween(0, node.content.size, '', '\ufffc');
    const foldedText = foldSearchText(text);
    for (let i = foldedText.indexOf(foldedNeedle); i !== -1; i = foldedText.indexOf(foldedNeedle, i + needle.length))
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
    view.focus();
    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, hit.from, hit.to)).scrollIntoView());
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
  if (event.key === 'Escape') {
    if (!$('link-field').hidden) { event.preventDefault(); hideLink(true); }
    else if (!$('find-bar').hidden) { event.preventDefault(); showSearch(false); }
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'f') { event.preventDefault(); showSearch(true); }
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
      matches();
      hideLink();
      queue = queue.then(() => load(markdown, id));
    },
    setTheme(theme) {
      for (const [key, value] of Object.entries(theme)) if (/^#[0-9a-f]{6}$/i.test(value)) document.documentElement.style.setProperty(`--${key.replaceAll('_', '-')}`, value);
      document.documentElement.style.colorScheme = theme.name === 'dark' ? 'dark' : 'light';
    },
    // Resume after an aborted close or a note-switch flush, using container state.
    setReadOnly(value) { closing = false; readOnly = value; controls(); },
    cancelClose() { closing = false; controls(); },
    async prepareClose(isCurrent, cancelled) {
      await Promise.race([queue, cancelled]);
      if (!isCurrent()) return;
      await Promise.race([finishComposition(false, isCurrent), cancelled]);
      if (!isCurrent()) return;
      closing = true;
      controls();
    },
  });
  try { await ensureEditor(); } catch (error) { console.error('Editor creation deferred to first load', error); }
  controls();
  bridge.ready();
} catch (error) { console.error('Editor startup failed', error); }
