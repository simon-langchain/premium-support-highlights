import { StyleSheet } from "@react-pdf/renderer";

// Colours mirror backend/report.py (the HTML report and email) so every export looks alike
export const colors = {
  text: "#111827",
  heading: "#374151",
  muted: "#6b7280",
  caption: "#9ca3af",
  border: "#e5e7eb",
  rowBorder: "#f3f4f6",
  accent: "#006ddd",
  closed: "#10b981",
  track: "#f3f4f6",
  summaryBg: "#f0f7ff",
  summaryText: "#1e3a5f",
  groupHeaderBg: "#eff6ff",
  groupHeaderBorder: "#dbeafe",
  groupHeaderText: "#1e40af",
  groupTickets: "#1e3a8a",
  groupReason: "#475569",
  groupRowBg: "#f8fbff",
  groupRowBorder: "#e0ecfb",
  footerBg: "#f8f7ff",
};

export const PAGE_PADDING_X = 36;
const PAGE_PADDING_Y = 36;

export const styles = StyleSheet.create({
  page: {
    fontFamily: "Inter",
    fontSize: 9.5,
    lineHeight: 1.45,
    color: colors.text,
    paddingTop: PAGE_PADDING_Y,
    paddingBottom: PAGE_PADDING_Y + 12, // room for the fixed page footer
    paddingHorizontal: PAGE_PADDING_X,
  },
  // Full-bleed banner on the first page: cancels the page padding
  banner: {
    marginTop: -PAGE_PADDING_Y,
    marginHorizontal: -PAGE_PADDING_X,
    marginBottom: 24,
  },
  // Fixed on every page; two absolutely positioned Texts (a positioned View is laid out at the top)
  pageFooterLeft: { position: "absolute", bottom: 20, left: PAGE_PADDING_X, fontSize: 7.5, color: colors.caption },
  // Full width + right-aligned: its text is only known after layout, so it can't size itself
  pageFooterRight: { position: "absolute", bottom: 20, left: PAGE_PADDING_X, right: PAGE_PADDING_X, textAlign: "right", fontSize: 7.5, color: colors.caption },

  // Header
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingBottom: 16,
    borderBottomWidth: 1.5,
    borderBottomColor: colors.accent,
    marginBottom: 8,
  },
  brandRow: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 6 },
  brandLabel: {
    fontSize: 8,
    fontWeight: 600,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    color: colors.accent,
  },
  accountName: { fontSize: 21, fontWeight: 700, lineHeight: 1.2 },
  headerMeta: { fontSize: 9, color: colors.muted, textAlign: "right" },

  sectionHeading: {
    fontSize: 9.5,
    fontWeight: 700,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    color: colors.heading,
    marginTop: 22,
    marginBottom: 9,
  },

  // Key metrics
  cardRow: { flexDirection: "row", gap: 8, marginBottom: 8 },
  card: {
    flex: 1,
    borderWidth: 0.75,
    borderColor: colors.border,
    borderRadius: 6,
    paddingVertical: 9,
    paddingHorizontal: 10,
  },
  cardLabel: {
    fontSize: 7.5,
    fontWeight: 600,
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: colors.muted,
    marginBottom: 4,
  },
  cardValue: { fontSize: 17, fontWeight: 700, lineHeight: 1.25 },
  cardUnit: { fontSize: 10, fontWeight: 400, color: colors.muted },
  cardSub: { fontSize: 8.5, color: colors.muted, marginTop: 1 },

  // Ticket trend
  trendBox: { borderWidth: 0.75, borderColor: colors.border, borderRadius: 6 },
  trendRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 5,
    paddingHorizontal: 9,
    borderTopWidth: 0.75,
    borderTopColor: colors.rowBorder,
  },
  trendHeaderRow: { flexDirection: "row", paddingVertical: 6, paddingHorizontal: 9 },
  trendHeaderCell: {
    fontSize: 8,
    fontWeight: 600,
    letterSpacing: 0.4,
    textTransform: "uppercase",
  },
  trendMonth: { width: 44, fontSize: 9, color: colors.muted },
  trendHeaderMonth: { width: 44, color: colors.muted },
  trendSeries: { flex: 1, flexDirection: "row", alignItems: "center", paddingRight: 10 },
  trendTrack: { flex: 1, height: 9 },
  trendBar: { height: 9, borderRadius: 1.5, opacity: 0.85 },
  trendCount: { width: 26, textAlign: "right", fontSize: 9, fontWeight: 600, color: colors.heading },

  // Breakdowns
  breakdownRow: { flexDirection: "row", gap: 8 },
  breakdownBox: {
    flex: 1,
    borderWidth: 0.75,
    borderColor: colors.border,
    borderRadius: 6,
    padding: 11,
  },
  breakdownTitle: {
    fontSize: 8,
    fontWeight: 700,
    letterSpacing: 0.5,
    textTransform: "uppercase",
    color: colors.muted,
    marginBottom: 6,
  },
  breakdownItem: { flexDirection: "row", alignItems: "center", paddingVertical: 3.5 },
  breakdownLabel: { flex: 1, fontSize: 9, color: colors.heading, paddingRight: 4 },
  breakdownCount: { width: 20, textAlign: "right", fontSize: 9, fontWeight: 600, color: colors.heading },
  breakdownTrack: { width: 34, height: 4.5, marginLeft: 7, borderRadius: 3, backgroundColor: colors.track },
  breakdownBar: { height: 4.5, borderRadius: 3, backgroundColor: colors.accent },

  // Account summary
  summaryBox: {
    backgroundColor: colors.summaryBg,
    borderLeftWidth: 2.5,
    borderLeftColor: colors.accent,
    borderTopRightRadius: 6,
    borderBottomRightRadius: 6,
    paddingVertical: 12,
    paddingHorizontal: 15,
  },
  summaryParagraph: { fontSize: 9.5, lineHeight: 1.65, color: colors.summaryText },
  summaryBullet: { flexDirection: "row", fontSize: 9.5, lineHeight: 1.65, color: colors.summaryText },
  bold: { fontWeight: 700 },

  // Open issues
  ticketRow: {
    flexDirection: "row",
    paddingVertical: 9,
    paddingHorizontal: 9,
    borderTopWidth: 0.75,
    borderTopColor: colors.rowBorder,
  },
  ticketRowGrouped: { backgroundColor: colors.groupRowBg, borderTopColor: colors.groupRowBorder },
  ticketLeft: { paddingRight: 9 },
  memberLabel: { flexDirection: "row", alignItems: "center", gap: 3.5, marginBottom: 3 },
  memberDot: { width: 6, height: 6, borderRadius: 3 },
  memberText: { fontSize: 8, fontWeight: 600, color: colors.heading },
  ticketNumber: { fontSize: 9, fontWeight: 600, color: colors.caption, textDecoration: "none" },
  ticketBody: { flex: 1, paddingRight: 9 },
  ticketTitle: { fontSize: 9.5, fontWeight: 600 },
  disposition: { fontSize: 8, fontWeight: 400, color: colors.caption },
  linked: { fontSize: 8, color: colors.muted, marginTop: 2 },
  linkedIds: { fontFamily: "Courier-Bold", fontSize: 8.5, color: colors.heading },
  ticketText: { fontSize: 9, color: colors.muted, marginTop: 4, lineHeight: 1.5 },
  ticketTextLabel: { fontWeight: 600, color: colors.heading },
  raisedBy: { fontSize: 8, color: colors.caption, marginTop: 4 },
  ticketRight: { width: 104, alignItems: "flex-end" },
  badge: {
    fontSize: 8,
    fontWeight: 600,
    borderWidth: 0.75,
    borderRadius: 3,
    paddingHorizontal: 5,
    paddingVertical: 1,
    marginBottom: 3,
  },
  age: { fontSize: 8, color: colors.caption },

  // Possible-duplicate group
  group: { borderLeftWidth: 2.5, borderLeftColor: colors.accent },
  groupHeader: {
    backgroundColor: colors.groupHeaderBg,
    borderTopWidth: 0.75,
    borderTopColor: colors.groupHeaderBorder,
    paddingTop: 8,
    paddingBottom: 6,
    paddingHorizontal: 9,
  },
  groupTitle: { fontSize: 8, fontWeight: 700, letterSpacing: 0.2, color: colors.groupHeaderText },
  groupReasonRow: { flexDirection: "row", marginTop: 2 },
  groupReasonTickets: { fontSize: 8, fontWeight: 700, color: colors.groupTickets, marginRight: 9 },
  groupReasonText: { flex: 1, fontSize: 8, lineHeight: 1.45, color: colors.groupReason },

  empty: { fontSize: 9.5, color: colors.caption },

  // Closing footer (as on the HTML report)
  copyright: {
    marginTop: 32,
    borderTopWidth: 1.5,
    borderTopColor: "#000",
    paddingVertical: 8,
    backgroundColor: colors.footerBg,
    fontSize: 8.5,
    fontStyle: "italic",
    color: "#333",
    textAlign: "center",
  },
});
