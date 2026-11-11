// Groups (#1039). A session belongs to a named group (SpawnSpec.group on the
// server: a `grp-` key, or `grp-…@server` across servers); the bar and the
// Fleet draw each group as one block headed by the group's own name, never by a
// session. Pure functions over metas.

const tool = (m) => m.kind === "term" || m.kind === "browser";

// A group's key, the same on every server: `<group>@<server>`. `here` names
// this server, for its own sessions, which carry no `server`.
export function groupKey(m, here) {
  const at = m.server || here;
  if (tool(m) || !m.group) return `${m.log_id || m.key}@${at}`;
  return m.group.includes("@") ? m.group : `${m.group}@${at}`;
}

// A group's colour, --group-0 to --group-3 (client.css), from its key: the bar
// and the Fleet paint one group alike.
export function hueOf(key) {
  let h = 0;
  for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % 4;
}

// The marks a folded block pops out of it, in the order they matter (#1039).
export const POP = ["needs_you", "review", "done", "error"];

// The marks a block's head shows, most urgent first: tabs.js's order, with
// done last for a group whose every member has finished.
const URGENT = ["needs_you", "error", "review", "working", "waiting", "done"];

// A group's name: any member's `group_name` (the server sends it with each
// wire). A far key its server has not migrated yet is a log id, named by the
// handle of the session it names, else by the key itself.
function nameOf(key, ms, handles) {
  const named = ms.find((m) => m.group_name);
  if (named) return named.group_name;
  const bare = key.split("@")[0];
  if (/^grp-[0-9a-f]+$/.test(bare)) return key; // its record is not here (yet)
  return handles.get(bare) || key;
}

// The blocks in bar order: [{key, head, members}]. A block of two or more open
// members is headed by a synthetic entry for the group (its name, key and most
// urgent mark); anything else by its lone member's own meta. `members` never
// includes the head, and a block stands where its first member stands in
// `metas`, which is in the browser's tab order (tabs.js TabOrder).
export function blocksOf(metas, here) {
  const groups = new Map();
  for (const m of metas) {
    const k = groupKey(m, here);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(m);
  }
  const handles = new Map(metas.filter((m) => m.log_id).map((m) => [m.log_id, m.handle]));
  return [...groups].map(([key, ms]) => {
    const on = ms.filter((m) => !m.off);
    const open = on.length ? on : ms;
    if (open.length < 2) {
      const head = open[0];
      return { key, head, members: ms.filter((m) => m !== head) };
    }
    const mark = URGENT.find((a) => open.some((m) => m.mark === a)) || open[0].mark;
    return {
      key,
      head: { kind: "group", key, name: nameOf(key, ms, handles), mark },
      members: ms,
    };
  });
}

// What a folded block draws beside itself: the members whose turn ended on a
// mark that needs the person, until their next turn starts, and the member in
// view (`focusId`), whatever its mark, so the block stays folded (#1039).
export function poppedOut(block, folded, focusId) {
  if (!folded || block.head.kind !== "group") return [];
  return block.members.filter((m) => POP.includes(m.mark) || m.key === focusId);
}
