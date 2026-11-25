import {
  addColAfterCommand, addColBeforeCommand, addRowAfterCommand, addRowBeforeCommand,
  deleteSelectedCellsCommand, selectColCommand, selectRowCommand,
} from "@milkdown/kit/preset/gfm";
import { commandsCtx } from "@milkdown/kit/core";
import { closeHistory } from "@milkdown/kit/prose/history";
import { Selection, TextSelection } from "@milkdown/kit/prose/state";
import { CellSelection, TableMap, cellAround, selectedRect } from "@milkdown/kit/prose/tables";

const operations = {
  'row-before': { command: addRowBeforeCommand, allowed: ({ top }) => top > 0 },
  'row-after': { command: addRowAfterCommand, rowOffset: 1 },
  'row-delete': { command: deleteSelectedCellsCommand, select: selectRowCommand,
    index: ({ top }) => top, allowed: ({ top, map }) => top > 0 && map.height > 2 },
  'col-before': { command: addColBeforeCommand },
  'col-after': { command: addColAfterCommand, colOffset: 1 },
  'col-delete': { command: deleteSelectedCellsCommand, select: selectColCommand,
    index: ({ left }) => left, allowed: ({ map }) => map.width > 1 },
};

export function tableContext(state) {
  const selection = state.selection;
  if (!(selection instanceof TextSelection)) return null;
  const cell = cellAround(selection.$from);
  if (!cell || cell.pos !== cellAround(selection.$to)?.pos) return null;
  const rect = selectedRect(state);
  // Markdown can only round-trip rectangular tables without spanning cells.
  if (rect.map.problems || rect.table.children.some(row => row.childCount !== rect.map.width)) return null;
  return rect;
}

export function tableOperationAllowed(name, rect) {
  return Boolean(rect && (operations[name].allowed?.(rect) ?? true));
}

export function runTableOperation(ctx, view, name) {
  const { state } = view;
  const rect = tableContext(state);
  if (!tableOperationAllowed(name, rect)) return;
  const operation = operations[name];
  const commands = ctx.get(commandsCtx);
  let commandState = state;
  if (operation.select) {
    // Select the line on a temporary state. The live editor sees only the
    // deletion, so undo can restore the original text selection as well.
    commands.get(operation.select.key)({ index: operation.index(rect) })(state, tr => {
      commandState = state.apply(tr);
    });
    const selection = commandState.selection;
    if (!(selection instanceof CellSelection) || (selection.isRowSelection() && selection.isColSelection())) return;
  }

  commands.get(operation.command.key)()(commandState, tr => {
    const table = tr.doc.nodeAt(rect.tableStart - 1);
    const map = TableMap.get(table);
    const row = Math.min(rect.top + (operation.rowOffset ?? 0), map.height - 1);
    const col = Math.min(rect.left + (operation.colOffset ?? 0), map.width - 1);
    const cell = rect.tableStart + map.positionAt(row, col, table);
    tr.setSelection(Selection.near(tr.doc.resolve(cell + 1)));
    view.dispatch(closeHistory(tr).scrollIntoView());
    // Give immediately following typing its own undo step.
    view.dispatch(closeHistory(view.state.tr));
  });
}
