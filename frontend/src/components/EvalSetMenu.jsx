import React from "react";
import Badge from "./ui/Badge.jsx";
import Menu, { MenuItem, MenuSeparator } from "./ui/Menu.jsx";
import { IconDownload, IconFileText, IconGear, IconTrash } from "./icons.jsx";

// The overflow menu for one eval set, wherever that set is on screen — the card
// in the grid, and the header of the set's own page.
//
// It lives in one component because it was written twice and the two copies
// drifted, which is a thing the developer sees rather than a thing hidden in the
// source: the same set offered "Download…" in the grid and "Download results…"
// one click later, and "Edit questions" existed only on the inner page, so the
// menu you had just used no longer had the item you had just seen. A menu whose
// contents depend on how you arrived at the set teaches nothing that survives
// the next click.
//
// Delete is the deliberate exception. It is passed only by the grid, because
// deleting the thing you are currently looking *at* leaves the developer on a
// page for a set that no longer exists; from the grid, the card simply goes.
export default function EvalSetMenu({
  label,
  owner,
  // The owner has never opened the settings for this set, so nobody has looked
  // at how it is graded. Surfaced on the item that leads there.
  unreviewedJudging = false,
  onDownload,
  onEditQuestions,
  onConfigure,
  onDelete,
}) {
  return (
    <Menu label={label}>
      {/* Download is offered to every role. A viewer can already read every row
          an export contains, so withholding the file would protect nothing while
          denying it to most of the people who want it. */}
      <MenuItem icon={<IconDownload size={15} />} onClick={onDownload}>
        Download
      </MenuItem>

      {owner && <MenuSeparator />}
      {owner && (
        <MenuItem icon={<IconFileText size={15} />} onClick={onEditQuestions}>
          Edit questions
        </MenuItem>
      )}
      {owner && (
        <MenuItem
          icon={<IconGear size={15} />}
          onClick={onConfigure}
          title={unreviewedJudging ? "Nobody has reviewed how this set is graded yet" : undefined}
        >
          Settings
          {unreviewedJudging && <Badge tone="warning" size="sm">review grading</Badge>}
        </MenuItem>
      )}

      {onDelete && <MenuSeparator />}
      {onDelete && (
        <MenuItem icon={<IconTrash size={15} />} variant="danger" onClick={onDelete}>
          Delete eval set
        </MenuItem>
      )}
    </Menu>
  );
}
