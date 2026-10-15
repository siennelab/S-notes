import { $command, $markSchema, $remark } from "@milkdown/kit/utils";
import remarkDirective from "remark-directive";

const supported = (size) => size === "15" || size === "20" || size === "24";
const directives = $remark("bodySizeDirective", () => remarkDirective);
const validSize = (node) => node.type === "textDirective" && node.name === "size"
  && supported(node.attributes?.size) && Object.keys(node.attributes).length === 1;

// Keep the existing remark parser. Unknown extension AST nodes become literal
// source text BEFORE Milkdown's schema consumes them; never drop their contents.
const literalDirectives = $remark("literalUnknownDirectives", () => () => (tree, file) => {
  const source = String(file.value);
  function visit(parent) {
    parent.children = parent.children?.map(node => {
      if (["textDirective", "leafDirective", "containerDirective"].includes(node.type) && !validSize(node)) {
        const raw = source.slice(node.position.start.offset, node.position.end.offset);
        const text = { type: "text", value: raw };
        return node.type === "textDirective" ? text : { type: "paragraph", children: [text] };
      }
      if (node.children) visit(node);
      return node;
    });
  }
  visit(tree);
});

// A Markdown mark, not an HTML converter. DOM spans exist only for rendering.
export const bodySize = $markSchema("body_size", () => ({
  priority: 40,
  // Keep the mark active at its trailing edge so normal ProseMirror typing
  // continues a sized run. Choosing 17 at a caret explicitly clears it below.
  inclusive: true,
  attrs: {
    size: {
      validate: (value) => {
        if (!supported(value)) throw new Error("Body size must be 15, 20, or 24");
      },
    },
  },
  parseDOM: [
    {
      tag: "span[data-body-size]",
      getAttrs: (element) => {
        const size = element.getAttribute("data-body-size");
        return supported(size) ? { size } : false;
      },
    },
  ],
  toDOM: (mark) => ["span", { "data-body-size": mark.attrs.size }, 0],
  parseMarkdown: {
    match: validSize,
    runner: (state, node, type) => {
      state.openMark(type, { size: node.attributes.size });
      state.next(node.children);
      state.closeMark(type);
    },
  },
  toMarkdown: {
    match: (mark) => mark.type.name === "body_size",
    runner: (state, mark) => {
      state.withMark(mark, "textDirective", undefined, {
        name: "size",
        attributes: { size: mark.attrs.size },
      });
    },
  },
}));

// Paragraph selections keep their existing inline-size semantics. A caret in
// a heading converts and sizes the whole heading; an explicit selection sizes
// only that selection after converting its heading blocks.
export const setBodySize = $command(
  "SetBodySize",
  (ctx) => (size) => (state, dispatch) => {
    if (![15, 17, 20, 24].includes(size)) return false;
    const { from, to, empty } = state.selection;
    const blocks = [];
    state.doc.nodesBetween(from, to, (node, pos) => {
      if (["paragraph", "heading"].includes(node.type.name)) blocks.push({ node, pos });
    });
    if (!blocks.length) return false;
    const headings = blocks.filter(({ node }) => node.type.name === "heading");
    const type = bodySize.type(ctx);
    const tr = state.tr;
    if (empty && !headings.length) {
      tr.removeStoredMark(type);
      if (size !== 17) tr.addStoredMark(type.create({ size: String(size) }));
      dispatch?.(tr.scrollIntoView());
      return true;
    }
    for (const { pos } of headings) tr.setNodeMarkup(pos, state.schema.nodes.paragraph);
    const start = empty ? headings[0].pos + 1 : from;
    const end = empty ? start + headings[0].node.content.size : to;
    for (const { node, pos } of blocks) {
      const blockStart = Math.max(start, pos + 1);
      const blockEnd = Math.min(end, pos + 1 + node.content.size);
      if (blockStart >= blockEnd) continue;
      tr.removeMark(blockStart, blockEnd, type);
      if (size !== 17) tr.addMark(blockStart, blockEnd, type.create({ size: String(size) }));
    }
    // A caret stays a caret when a heading is converted, so make its next
    // input agree with the body size just applied to that whole block. Range
    // selections can rely on the marks at whichever edge the user collapses.
    if (empty) {
      tr.removeStoredMark(type);
      if (size !== 17) tr.addStoredMark(type.create({ size: String(size) }));
    }
    dispatch?.(tr.scrollIntoView());
    return true;
  },
);

// Heading is a block style: clear inline body sizes throughout every affected
// heading, even when the caret/selection covers only part of its text.
export const setHeading = $command(
  "SetCleanHeading",
  (ctx) => (level) => (state, dispatch) => {
    if (![1, 2, 3].includes(level)) return false;
    const { from, to } = state.selection;
    const type = bodySize.type(ctx);
    const tr = state.tr.setBlockType(from, to, state.schema.nodes.heading, { level });
    tr.doc.nodesBetween(from, to, (node, pos) => {
      if (node.type.name === "heading") tr.removeMark(pos + 1, pos + 1 + node.content.size, type);
    });
    dispatch?.(tr.removeStoredMark(type).scrollIntoView());
    return true;
  },
);

export const bodySizeExtension = [directives, literalDirectives, bodySize, setBodySize, setHeading].flat();
