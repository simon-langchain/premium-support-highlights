export interface MemberLabel {
  label: string;
  fullName: string;
  color: string;
}

// Dot colours for member accounts: slots into the --member-N palette tokens
// (globals.css), chosen per member in the Account Groups menu. The text label
// always names the account, so the colour is never the only cue.
export const MEMBER_COLOR_SLOTS = [1, 2, 3, 4, 5, 6, 7, 8];
export const MEMBER_COLOR_NAMES: Record<number, string> = {
  1: "Blue", 2: "Orange", 3: "Teal", 4: "Amber", 5: "Pink", 6: "Green", 7: "Violet", 8: "Red",
};

// Default for members without a chosen colour: slots in order.
export function defaultMemberSlot(index: number): number {
  return (index % MEMBER_COLOR_SLOTS.length) + 1;
}

export function memberColor(slot: number): string {
  return `var(--member-${slot})`;
}

/** Label (custom, else default) and dot colour for each member of a group. */
export function memberLabels(
  members: { id: string; name: string; color?: number; label?: string | null; default_label?: string }[],
): Map<string, MemberLabel> {
  return new Map(
    members.map((m, i) => [
      m.id,
      {
        // default_label is derived by the backend (account_groups.default_member_labels)
        label: m.label || m.default_label || m.name,
        fullName: m.name,
        color: memberColor(m.color ?? defaultMemberSlot(i)),
      },
    ]),
  );
}
