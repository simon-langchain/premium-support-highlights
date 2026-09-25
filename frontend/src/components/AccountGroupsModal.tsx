"use client";

import { useState, useEffect } from "react";
import { X, ArrowLeft, Plus, Layers, Loader2, Search, Check, Unlink } from "lucide-react";
import {
  fetchAccountGroups,
  fetchGroupCandidates,
  createAccountGroup,
  deleteAccountGroup,
  updateAccountGroupMembers,
  type AccountGroup,
  type GroupCandidate,
} from "@/lib/api";
import {
  MEMBER_COLOR_NAMES,
  MEMBER_COLOR_SLOTS,
  defaultMemberSlot,
  memberColor,
} from "@/lib/accountLabels";

const MAX_GROUP_MEMBERS = 10; // matches AccountGroupRequest in backend/main.py

/** Inline label editor: looks like text until hovered/focused; saves on blur or Enter, Escape reverts. */
function LabelInput({
  value,
  placeholder,
  onSave,
  ariaLabel,
}: {
  value: string;
  placeholder: string;
  onSave: (value: string) => void;
  ariaLabel: string;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <input
      type="text"
      value={draft}
      maxLength={40}
      placeholder={placeholder}
      aria-label={ariaLabel}
      title="Label shown on tickets — click to edit"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => onSave(draft)}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") { setDraft(value); setTimeout(() => (e.target as HTMLInputElement).blur(), 0); }
      }}
      className="text-xs font-medium rounded px-1.5 py-0.5 w-28 flex-shrink-0 outline-none border border-transparent hover:border-[var(--border)] focus:border-[#006ddd] transition-colors placeholder:text-[var(--text-caption)]"
      style={{ background: "transparent", color: "var(--text-primary)" }}
    />
  );
}

export default function AccountGroupsModal({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  /** Called after a group is created (with it), removed (null + removedId) or recoloured (null). */
  onChanged: (created: AccountGroup | null, removedId?: string) => void;
}) {
  const [view, setView] = useState<"list" | "form">("list");
  const [groups, setGroups] = useState<AccountGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmUngroup, setConfirmUngroup] = useState<AccountGroup | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [colorPicker, setColorPicker] = useState<{ groupId: string; memberId: string } | null>(null);

  // Form state
  const [candidates, setCandidates] = useState<GroupCandidate[] | null>(null);
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchAccountGroups()
      .then(setGroups)
      .catch((e) => setError(e.message));
  }, []);

  function openForm() {
    setView("form");
    setError(null);
    setName("");
    setQuery("");
    setSelectedIds([]);
    setCandidates(null);
    fetchGroupCandidates()
      .then(setCandidates)
      .catch((e) => setError(e.message));
  }

  function toggle(id: string) {
    setSelectedIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : ids.length < MAX_GROUP_MEMBERS ? [...ids, id] : ids,
    );
  }

  async function handleCreate() {
    setSaving(true);
    setError(null);
    try {
      const group = await createAccountGroup(name.trim(), selectedIds);
      setGroups((gs) => [...(gs ?? []), group].sort((a, b) => a.name.localeCompare(b.name)));
      setView("list");
      onChanged(group);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  // Optimistically apply a member change, save it, and roll back on failure.
  async function updateMember(
    group: AccountGroup,
    memberId: string,
    patch: { color?: number; label?: string | null },
    changes: Parameters<typeof updateAccountGroupMembers>[1],
  ) {
    const replace = (updated: AccountGroup) => setGroups((gs) => (gs ?? []).map((g) => (g.id === updated.id ? updated : g)));
    // Apply/undo just this member's fields, so edits to other members made meanwhile survive
    const setMember = (fields: Partial<AccountGroup["members"][number]>) =>
      setGroups((gs) => (gs ?? []).map((g) =>
        g.id === group.id ? { ...g, members: g.members.map((m) => (m.id === memberId ? { ...m, ...fields } : m)) } : g,
      ));
    const before = group.members.find((m) => m.id === memberId);
    setMember(patch);
    setError(null);
    try {
      replace(await updateAccountGroupMembers(group.id, changes));
      onChanged(null);
    } catch (e) {
      if (before) setMember({ color: before.color, label: before.label });
      setError((e as Error).message);
    }
  }

  function handleColor(group: AccountGroup, memberId: string, slot: number) {
    setColorPicker(null);
    const i = group.members.findIndex((m) => m.id === memberId);
    if ((group.members[i]?.color ?? defaultMemberSlot(i)) === slot) return;
    updateMember(group, memberId, { color: slot }, { member_colors: { [memberId]: slot } });
  }

  // Saving the default (or clearing the field) removes the custom label, so it keeps
  // tracking the default if the account names change.
  function handleLabel(group: AccountGroup, memberId: string, value: string) {
    const label = value.trim().replace(/\s+/g, " ");
    const member = group.members.find((m) => m.id === memberId);
    const fallback = member?.default_label ?? member?.name ?? "";
    const custom = label && label !== fallback ? label : null;
    if (custom === (group.members.find((m) => m.id === memberId)?.label || null)) return;
    updateMember(group, memberId, { label: custom }, { member_labels: { [memberId]: custom ?? "" } });
  }

  async function handleUngroup(group: AccountGroup) {
    setConfirmUngroup(null);
    setBusyId(group.id);
    setError(null);
    try {
      await deleteAccountGroup(group.id);
      setGroups((gs) => (gs ?? []).filter((g) => g.id !== group.id));
      onChanged(null, group.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  const filtered = (candidates ?? []).filter(
    (c) => !query.trim() || c.name.toLowerCase().includes(query.toLowerCase()),
  );
  const selectedNames = (candidates ?? []).filter((c) => selectedIds.includes(c.id)).map((c) => c.name);
  const canCreate = name.trim().length > 0 && selectedIds.length >= 2 && !saving;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.6)" }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="relative w-full max-w-lg rounded-xl overflow-hidden flex flex-col"
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          boxShadow: "0 16px 48px rgba(0,0,0,0.5)",
          maxHeight: "90vh",
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          <div className="flex items-center gap-2 min-w-0">
            {view === "form" && (
              <button
                onClick={() => { setView("list"); setError(null); }}
                className="rounded p-1 hover:bg-[var(--bg-tertiary)] transition-colors flex-shrink-0"
                style={{ color: "var(--text-muted)" }}
              >
                <ArrowLeft size={14} />
              </button>
            )}
            <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              {view === "list" ? "Account Groups" : "New Account Group"}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 hover:bg-[var(--bg-tertiary)] transition-colors"
            style={{ color: "var(--text-muted)" }}
          >
            <X size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 min-h-0">
          {error && (
            <p className="text-xs mb-3 rounded px-3 py-2" style={{ color: "var(--badge-red-text)", background: "var(--badge-red-bg)" }}>
              {error}
            </p>
          )}

          {view === "list" && (
            <>
              {groups === null && !error ? (
                <div className="flex items-center gap-2 py-6 justify-center" style={{ color: "var(--text-muted)" }}>
                  <Loader2 size={14} className="animate-spin" />
                  <span className="text-sm">Loading groups...</span>
                </div>
              ) : groups && groups.length === 0 ? (
                <p className="text-sm py-6 text-center" style={{ color: "var(--text-caption)" }}>No groups yet</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {(groups ?? []).map((g) => (
                    <div
                      key={g.id}
                      title={g.created_by ? `Created by ${g.created_by}` : undefined}
                      className="rounded-lg px-3 py-2.5 flex items-start justify-between gap-3"
                      style={{ background: "var(--bg-base)", border: "1px solid var(--border)" }}
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <Layers size={13} style={{ color: "var(--accent)" }} className="flex-shrink-0" />
                          <span className="text-sm font-medium truncate" style={{ color: "var(--text-primary)" }}>{g.name}</span>
                        </div>
                        <div className="flex flex-col gap-1.5 mt-2">
                          {g.members.map((m, i) => {
                            const slot = m.color ?? defaultMemberSlot(i);
                            const open = colorPicker?.groupId === g.id && colorPicker.memberId === m.id;
                            return (
                              <div key={m.id}>
                                <div className="flex items-center gap-2 min-w-0">
                                  <button
                                    onClick={() => setColorPicker(open ? null : { groupId: g.id, memberId: m.id })}
                                    title="Change colour"
                                    aria-label={`Change colour for ${m.name}`}
                                    className="w-3 h-3 rounded-full flex-shrink-0 transition-transform hover:scale-125"
                                    style={{ background: memberColor(slot) }}
                                  />
                                  <LabelInput
                                    value={m.label || m.default_label || m.name}
                                    placeholder={m.default_label || m.name}
                                    onSave={(v) => handleLabel(g, m.id, v)}
                                    ariaLabel={`Label for ${m.name}`}
                                  />
                                  <span className="text-xs truncate" style={{ color: "var(--text-caption)" }}>{m.name}</span>
                                </div>
                                {open && (
                                  <div className="flex items-center gap-1.5 mt-1.5 mb-0.5 pl-5">
                                    {MEMBER_COLOR_SLOTS.map((n) => (
                                      <button
                                        key={n}
                                        onClick={() => handleColor(g, m.id, n)}
                                        title={MEMBER_COLOR_NAMES[n]}
                                        aria-label={MEMBER_COLOR_NAMES[n]}
                                        className="w-4 h-4 rounded-full transition-transform hover:scale-110"
                                        style={{
                                          background: memberColor(n),
                                          outline: n === slot ? "2px solid var(--text-primary)" : "none",
                                          outlineOffset: 2,
                                        }}
                                      />
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                      {confirmUngroup?.id === g.id ? (
                        <div className="flex items-center gap-1.5 flex-shrink-0">
                          <button
                            onClick={() => setConfirmUngroup(null)}
                            className="text-xs px-2 py-1 rounded hover:bg-[var(--bg-tertiary)] transition-colors"
                            style={{ color: "var(--text-muted)" }}
                          >
                            Cancel
                          </button>
                          <button
                            onClick={() => handleUngroup(g)}
                            className="text-xs px-2 py-1 rounded transition-colors"
                            style={{ color: "#fff", background: "#dc2626" }}
                          >
                            Ungroup
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setConfirmUngroup(g)}
                          disabled={busyId === g.id}
                          title="Ungroup — list these accounts separately again"
                          className="flex items-center gap-1 text-xs px-2 py-1 rounded hover:bg-[var(--bg-tertiary)] transition-colors flex-shrink-0 disabled:opacity-50"
                          style={{ color: "var(--text-muted)" }}
                        >
                          {busyId === g.id ? <Loader2 size={12} className="animate-spin" /> : <Unlink size={12} />}
                          Ungroup
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {view === "form" && (
            <div className="flex flex-col gap-4">
              <div>
                <label className="block text-xs uppercase tracking-wider mb-1" style={{ color: "var(--text-muted)" }}>
                  Group name
                </label>
                <input
                  type="text"
                  value={name}
                  maxLength={80}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Orange"
                  className="w-full text-sm rounded px-2 py-1.5 outline-none placeholder:text-[var(--text-caption)]"
                  style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                />
              </div>

              <div>
                <label className="block text-xs uppercase tracking-wider mb-1" style={{ color: "var(--text-muted)" }}>
                  Accounts {selectedIds.length > 0 && `(${selectedIds.length} selected)`}
                </label>
                {selectedNames.length > 0 && (
                  <p className="text-xs mb-2" style={{ color: "var(--text-primary)" }}>{selectedNames.join(" + ")}</p>
                )}
                <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--bg-base)" }}>
                  <div style={{ borderBottom: "1px solid var(--border)" }} className="flex items-center gap-2 px-2.5 py-2">
                    <Search size={13} style={{ color: "var(--text-muted)" }} className="flex-shrink-0" />
                    <input
                      type="text"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Search accounts..."
                      style={{ background: "transparent", color: "var(--text-primary)" }}
                      className="flex-1 text-sm outline-none placeholder:text-[var(--text-caption)] min-w-0"
                    />
                  </div>
                  <div className="overflow-y-auto max-h-60">
                    {candidates === null ? (
                      <div className="flex items-center gap-2 px-3 py-3" style={{ color: "var(--text-muted)" }}>
                        <Loader2 size={13} className="animate-spin" />
                        <span className="text-xs">Loading accounts...</span>
                      </div>
                    ) : filtered.length === 0 ? (
                      <p style={{ color: "var(--text-caption)" }} className="text-xs px-3 py-2">No accounts match</p>
                    ) : (
                      filtered.map((c) => {
                        const checked = selectedIds.includes(c.id);
                        return (
                          <button
                            key={c.id}
                            onClick={() => toggle(c.id)}
                            className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left hover:bg-[var(--bg-tertiary)] transition-colors"
                            style={{ color: "var(--text-primary)" }}
                          >
                            <span
                              className="w-3.5 h-3.5 rounded-sm flex items-center justify-center flex-shrink-0"
                              style={{
                                border: checked ? "1px solid #006ddd" : "1px solid var(--text-caption)",
                                background: checked ? "#006ddd" : "transparent",
                              }}
                            >
                              {checked && <Check size={10} color="#fff" />}
                            </span>
                            <span className="truncate flex-1">{c.name}</span>
                            {c.tier && <span className="text-xs flex-shrink-0" style={{ color: "var(--text-caption)" }}>{c.tier}</span>}
                          </button>
                        );
                      })
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 flex-shrink-0 flex justify-end" style={{ borderTop: "1px solid var(--border)" }}>
          {view === "list" ? (
            <button
              onClick={openForm}
              className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded transition-colors"
              style={{ background: "#006ddd", color: "#fff" }}
            >
              <Plus size={14} />
              New Group
            </button>
          ) : (
            <button
              onClick={handleCreate}
              disabled={!canCreate}
              className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ background: "#006ddd", color: "#fff" }}
            >
              {saving && <Loader2 size={14} className="animate-spin" />}
              Create Group
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
