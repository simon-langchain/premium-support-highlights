import { Link, Text, View } from "@react-pdf/renderer";
import type { ReportBadge, ReportModel, ReportTicket } from "@/lib/api";
import { styles } from "./supportReportPdfStyles";

type TicketEntries = NonNullable<ReportModel["tickets"]>;

function Badge({ badge }: { badge: ReportBadge }) {
  return (
    <View style={[styles.badge, { backgroundColor: badge.bg, borderColor: badge.border }]}>
      <Text style={{ color: badge.color }}>{badge.label}</Text>
    </View>
  );
}

function TicketRow({ ticket, leftWidth, grouped }: { ticket: ReportTicket; leftWidth: number; grouped: boolean }) {
  return (
    <View style={[styles.ticketRow, grouped ? styles.ticketRowGrouped : {}]} wrap={false}>
      <View style={[styles.ticketLeft, { width: leftWidth }]}>
        {ticket.member && (
          <View style={styles.memberLabel}>
            <View style={[styles.memberDot, { backgroundColor: ticket.member.color }]} />
            <Text style={styles.memberText}>{ticket.member.label}</Text>
          </View>
        )}
        {ticket.portal_url ? (
          <Link src={ticket.portal_url} style={styles.ticketNumber}>#{ticket.number}</Link>
        ) : (
          <Text style={styles.ticketNumber}>#{ticket.number}</Text>
        )}
      </View>
      <View style={styles.ticketBody}>
        <Text style={styles.ticketTitle}>
          {ticket.title}
          {ticket.disposition && <Text style={styles.disposition}>{"  "}{ticket.disposition}</Text>}
        </Text>
        {ticket.linked_ids.length > 0 && (
          <Text style={styles.linked}>
            Linked: <Text style={styles.linkedIds}>{ticket.linked_ids.join("  ·  ")}</Text>
          </Text>
        )}
        {ticket.summary && (
          <Text style={styles.ticketText}>
            <Text style={styles.ticketTextLabel}>Summary: </Text>
            {ticket.summary}
          </Text>
        )}
        {ticket.next_steps && (
          <Text style={styles.ticketText}>
            <Text style={styles.ticketTextLabel}>Next steps: </Text>
            {ticket.next_steps}
          </Text>
        )}
        {ticket.requester && <Text style={styles.raisedBy}>Raised by {ticket.requester}</Text>}
      </View>
      <View style={styles.ticketRight}>
        <Badge badge={ticket.priority} />
        <Badge badge={ticket.state} />
        <Text style={styles.age}>{ticket.age}</Text>
      </View>
    </View>
  );
}

/** Open tickets in report order; possible-duplicate groups are one block with a header. */
export function SupportReportPdfTickets({ entries }: { entries: TicketEntries }) {
  if (entries.length === 0) return <Text style={styles.empty}>No open issues.</Text>;

  // Left column fits the longest member label on one line (account groups only)
  const labels = entries.flatMap((e) => e.tickets.map((t) => t.member?.label ?? ""));
  const longest = Math.max(0, ...labels.map((l) => l.length));
  const leftWidth = longest ? Math.max(44, 14 + longest * 4.8) : 44;

  return (
    <View>
      {entries.map((entry) => {
        const key = entry.tickets.map((t) => t.number).join("-");
        if (!entry.group) {
          return <TicketRow key={key} ticket={entry.tickets[0]} leftWidth={leftWidth} grouped={false} />;
        }
        return (
          <View key={key} style={styles.group}>
            {/* minPresenceAhead keeps the header on the same page as its first ticket */}
            <View style={styles.groupHeader} minPresenceAhead={60} wrap={false}>
              <Text style={styles.groupTitle}>POSSIBLE DUPLICATES · {entry.tickets.length} TICKETS</Text>
              {entry.group.reasons.map((r, i) => (
                <View key={i} style={styles.groupReasonRow}>
                  {r.tickets && (
                    <Text style={styles.groupReasonTickets}>{r.tickets.map((n) => `#${n}`).join(" + ")}</Text>
                  )}
                  <Text style={styles.groupReasonText}>{r.text}</Text>
                </View>
              ))}
            </View>
            {entry.tickets.map((t) => (
              <TicketRow key={t.number} ticket={t} leftWidth={leftWidth} grouped />
            ))}
          </View>
        );
      })}
    </View>
  );
}
