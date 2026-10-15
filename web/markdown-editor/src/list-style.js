import { extendListItemSchemaForTask } from "@milkdown/kit/preset/gfm";
import { $command, $prose } from "@milkdown/kit/utils";
import { liftListItem, wrapInList } from "@milkdown/kit/prose/schema-list";
import { Plugin } from "@milkdown/kit/prose/state";
import { Decoration, DecorationSet } from "@milkdown/kit/prose/view";

// Keep Milkdown's standard Markdown list parser/serializer and GFM task items.
// Only relax the first content block: headings are valid standard list content.
const listContent = extendListItemSchemaForTask.extendSchema(prev => ctx => ({
  ...prev(ctx),
  content: "(paragraph | heading) block*",
}));

export const toggleList = $command("ToggleContentList", () => kind => (state, dispatch) => {
  const listType = state.schema.nodes[kind];
  if (!["ordered_list", "bullet_list"].includes(kind)) return false;
  const { $from, $to } = state.selection;
  for (let depth = $from.depth - 1; depth > 0; depth--) {
    const list = $from.node(depth);
    if (!["ordered_list", "bullet_list"].includes(list.type.name)) continue;
    if ($to.depth < depth || $to.node(depth) !== list) break;
    if (list.type === listType) return liftListItem(state.schema.nodes.list_item)(state, dispatch);
    // Switch the enclosing wrapper. Normalize its direct item labels in the
    // same transaction so Milkdown's order synchronizer cannot restore it.
    const pos = $from.before(depth), tr = state.tr.setNodeMarkup(pos, listType, { spread: list.attrs.spread });
    list.forEach((item, offset, index) => {
      tr.setNodeMarkup(pos + 1 + offset, undefined, {
        ...item.attrs,
        listType: kind === "ordered_list" ? "ordered" : "bullet",
        label: kind === "ordered_list" ? `${index + 1}.` : "•",
      });
    });
    dispatch?.(tr.scrollIntoView());
    return true;
  }
  return wrapInList(listType)(state, dispatch);
});

// Derived presentation only. No node attributes, Markdown fields, or bridge data.
// The first block is the item's main style. Only a uniformly sized paragraph
// changes its marker; a mixed inline-size paragraph always uses default 20px.
function markerStyle(block) {
  if (block?.type.name === "heading") return `h${block.attrs.level}`;
  if (block?.type.name !== "paragraph") return "body20";
  const sizes = new Set();
  block.descendants(node => {
    if (node.isInline) sizes.add(node.marks.find(mark => mark.type.name === "body_size")?.attrs.size || "20");
  });
  return sizes.size === 1 ? `body${[...sizes][0]}` : "body20";
}
function decorations(doc) {
  const result = [];
  doc.descendants((node, pos) => {
    if (node.type.name === "list_item") result.push(Decoration.node(pos, pos + node.nodeSize, {
      "data-marker-style": markerStyle(node.firstChild),
    }));
  });
  return DecorationSet.create(doc, result);
}
const markerPresentation = $prose(() => new Plugin({
  state: {
    init: (_, state) => decorations(state.doc),
    apply: (tr, previous) => tr.docChanged ? decorations(tr.doc) : previous,
  },
  props: { decorations(state) { return this.getState(state); } },
}));

export const listStyleExtension = [listContent, toggleList, markerPresentation].flat();
