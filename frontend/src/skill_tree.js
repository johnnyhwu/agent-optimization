// One skill on the agent, drawn as the directory it actually is.
//
// The wizard used to describe a skill as "3 files on the agent · 4,820
// characters · billing/SKILL.md, billing/reference/a.md, billing/reference/b.md"
// — one comma-separated line in which every path repeats the skill name, and one
// total that cannot say whether this is a long SKILL.md or a short one beside a
// large reference. That distinction is the whole question on this step: an
// isolated run edits the body of SKILL.md, so a skill whose characters are
// nearly all in a reference file is a skill the run has little purchase on.
//
// Flattened to rows rather than left as a nested object because the caller is
// JSX and a recursive component for four lines of text is more machinery than
// the text is worth. Each row carries its own depth and the renderer indents by
// it.

// Rows, in display order: directories before the files beside them, then both
// alphabetically. The skill's own directory is always row 0, so a skill with one
// file still reads as a directory containing it rather than as a lone filename.
export function skillTree(skillName, files = [], fileChars = {}) {
  const root = { name: `${skillName}/`, dirs: new Map(), files: [] };

  for (const path of files) {
    // Paths arrive as `billing/reference/a.md`; the first segment is the skill
    // itself and is already the root's name. A path that is exactly the skill
    // name — a skill stored as a single file rather than a directory — has no
    // segments left and is filed under its own name.
    const segments = path.split("/");
    const rest = segments[0] === skillName ? segments.slice(1) : segments;
    const name = rest.pop() ?? skillName;
    let node = root;
    for (const dir of rest) {
      if (!node.dirs.has(dir)) {
        node.dirs.set(dir, { name: `${dir}/`, dirs: new Map(), files: [] });
      }
      node = node.dirs.get(dir);
    }
    node.files.push({ name, path, chars: fileChars[path] ?? null });
  }

  const rows = [{ depth: 0, name: root.name, isDir: true, path: null, chars: null }];
  walk(root, 1, rows);
  return rows;
}

function walk(node, depth, rows) {
  for (const dir of [...node.dirs.values()].sort(byName)) {
    rows.push({ depth, name: dir.name, isDir: true, path: null, chars: null });
    walk(dir, depth + 1, rows);
  }
  for (const file of node.files.sort(byName)) {
    rows.push({ depth, name: file.name, isDir: false, path: file.path, chars: file.chars });
  }
}

function byName(a, b) {
  return a.name.localeCompare(b.name);
}

// "2,410 characters", or nothing at all when the agent did not say. Not "0
// characters": an empty file and a file whose length was not reported are
// different claims, and only one of them is worth acting on.
export function charLabel(chars) {
  if (chars == null) return "";
  return `${chars.toLocaleString()} character${chars === 1 ? "" : "s"}`;
}
