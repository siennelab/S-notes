import { closeHistory } from "@milkdown/kit/prose/history";
import { Fragment } from "@milkdown/kit/prose/model";
import { TextSelection } from "@milkdown/kit/prose/state";
import { $command } from "@milkdown/kit/utils";

const supported = node => ["paragraph", "heading", "code_block"].includes(node.type.name);
const text = (node, from = 0, to = node.content.size) => node.textBetween(from, to, "\n", "\n");
const content = (schema, value) => value ? schema.text(value) : undefined;

function lineAt(value, offset) {
  let line = 0;
  for (let index = 0; index < offset; index++) if (value[index] === "\n") line += 1;
  return line;
}

function codeSelectionReplacement(state, entry, from, to) {
  const { node, pos } = entry;
  const value = text(node);
  const start = pos + 1;
  const localFrom = Math.max(0, Math.min(value.length, from - start));
  const localTo = Math.max(localFrom, Math.min(value.length, to - start));
  const lines = value.split("\n");
  const first = lineAt(value, localFrom);
  const last = localTo > localFrom ? lineAt(value, localTo - 1) : first;
  const result = [];
  const before = lines.slice(0, first).join("\n");
  const after = lines.slice(last + 1).join("\n");
  if (first > 0) result.push(node.type.create(node.attrs, content(state.schema, before)));
  for (const line of lines.slice(first, last + 1)) {
    result.push(state.schema.nodes.paragraph.create(undefined, content(state.schema, line)));
  }
  if (last + 1 < lines.length) result.push(node.type.create(node.attrs, content(state.schema, after)));
  return result;
}

function selectedBlocks(state) {
  const { from, to } = state.selection;
  const blocks = [];
  state.doc.nodesBetween(from, to, (node, pos, parent, index) => {
    if (!node.isTextblock) return true;
    blocks.push({ node, pos, parent, index });
    return false;
  });
  if (!blocks.length || blocks.some(({ node }) => !supported(node))) return null;
  const parent = blocks[0].parent;
  if (blocks.some(block => block.parent !== parent)) return null;
  const first = blocks[0].index;
  const last = blocks.at(-1).index;
  if (last - first + 1 !== blocks.length) return null;
  return { blocks, parent, first, last };
}

function toggleSelection(state, dispatch) {
  const found = selectedBlocks(state);
  if (!found) return false;
  const { blocks, parent, first, last } = found;
  const codeType = state.schema.nodes.code_block;
  const hasCode = blocks.some(({ node }) => node.type === codeType);
  if (!hasCode) {
    const value = blocks.map(({ node }) => text(node)).join("\n");
    const replacement = codeType.create({ language: "" }, content(state.schema, value));
    if (!parent.canReplace(first, last + 1, Fragment.from(replacement))) return false;
    const tr = state.tr.replaceWith(blocks[0].pos, blocks.at(-1).pos + blocks.at(-1).node.nodeSize, replacement);
    dispatch?.(closeHistory(tr).scrollIntoView());
    return true;
  }

  const replacements = blocks
    .filter(({ node }) => node.type === codeType)
    .map(entry => ({ ...entry, replacement: codeSelectionReplacement(state, entry, state.selection.from, state.selection.to) }));
  for (const { parent: blockParent, index, replacement } of replacements) {
    if (!blockParent.canReplace(index, index + 1, Fragment.fromArray(replacement))) return false;
  }
  const tr = state.tr;
  for (const { node, pos, replacement } of replacements.reverse()) {
    tr.replaceWith(pos, pos + node.nodeSize, Fragment.fromArray(replacement));
  }
  dispatch?.(closeHistory(tr).scrollIntoView());
  return true;
}

function toggleCaret(state, dispatch) {
  const { $from } = state.selection;
  const node = $from.parent;
  if (!supported(node)) return false;
  const depth = $from.depth;
  const parent = $from.node(depth - 1);
  const index = $from.index(depth - 1);
  const pos = $from.before(depth);
  const offset = $from.parentOffset;
  const codeType = state.schema.nodes.code_block;
  const paragraphType = state.schema.nodes.paragraph;
  let replacement;
  let caretIndex;

  if (node.type !== codeType) {
    const right = text(node, offset);
    const code = codeType.create({ language: "" }, content(state.schema, right));
    replacement = offset > 0 ? [node.copy(node.content.cut(0, offset)), code] : [code];
    caretIndex = replacement.length - 1;
  } else if (offset === 0) {
    replacement = text(node).split("\n").map(line => paragraphType.create(undefined, content(state.schema, line)));
    caretIndex = 0;
  } else if (offset === node.content.size) {
    replacement = [node, paragraphType.create()];
    caretIndex = 1;
  } else {
    const left = text(node, 0, offset);
    const right = text(node, offset).split("\n")
      .map(line => paragraphType.create(undefined, content(state.schema, line)));
    replacement = left ? [codeType.create(node.attrs, state.schema.text(left)), ...right] : right;
    caretIndex = left ? 1 : 0;
  }

  const fragment = Fragment.fromArray(replacement);
  if (!parent.canReplace(index, index + 1, fragment)) return false;
  const tr = state.tr.replaceWith(pos, pos + node.nodeSize, fragment);
  let caret = pos + 1;
  for (let child = 0; child < caretIndex; child++) caret += replacement[child].nodeSize;
  tr.setSelection(TextSelection.create(tr.doc, caret));
  dispatch?.(closeHistory(tr).scrollIntoView());
  return true;
}

export const toggleCodeBlock = $command(
  "ToggleSplitCodeBlock",
  () => () => (state, dispatch) => state.selection.empty
    ? toggleCaret(state, dispatch)
    : toggleSelection(state, dispatch),
);
