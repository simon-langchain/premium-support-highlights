import { Document, Font, Image, Page, Path, Svg, Text, View, pdf } from "@react-pdf/renderer";
import type { ReportMetricCard, ReportModel } from "@/lib/api";
import { SupportReportPdfTickets } from "./SupportReportPdfTickets";
import { colors, styles } from "./supportReportPdfStyles";

let fontsRegistered = false;

/** Inter (public/fonts/inter, OFL) instead of the built-in Helvetica, which can't draw
 * characters outside Latin-1 that turn up in customer names and ticket titles.
 * `base`: where the TTFs are served, e.g. `${location.origin}/fonts/inter`. */
export function registerReportFonts(base: string) {
  if (fontsRegistered) return;
  Font.register({
    family: "Inter",
    fonts: [
      { src: `${base}/Inter-Regular.ttf` },
      { src: `${base}/Inter-Italic.ttf`, fontStyle: "italic" },
      { src: `${base}/Inter-SemiBold.ttf`, fontWeight: 600 },
      { src: `${base}/Inter-Bold.ttf`, fontWeight: 700 },
    ],
  });
  // No automatic hyphenation: it splits ticket titles and IDs mid-word
  Font.registerHyphenationCallback((word) => [word]);
  fontsRegistered = true;
}

function Logo() {
  return (
    <Svg width={15} height={15} viewBox="0 0 128 128">
      <Path fill={colors.accent} d="M40.1024 85.0722C47.6207 77.5537 51.8469 67.3453 51.8469 56.7136C51.8469 46.0818 47.617 35.8734 40.1024 28.355L11.7446 0C4.22995 7.5185 0 17.7269 0 28.3586C0 38.9903 4.22995 49.1987 11.7446 56.7172L40.0987 85.0722H40.1024Z" />
      <Path fill={colors.accent} d="M99.4385 87.698C91.9239 80.1832 81.7121 75.9531 71.0844 75.9531C60.4566 75.9531 50.2448 80.1832 42.7266 87.698L71.0844 116.057C78.599 123.571 88.8107 127.802 99.4421 127.802C110.074 127.802 120.282 123.571 127.8 116.057L99.4421 87.698H99.4385Z" />
      <Path fill={colors.accent} d="M11.8146 115.987C19.3329 123.502 29.541 127.732 40.1724 127.732V87.6289H0.0664062C0.0700559 98.2606 4.29635 108.469 11.8146 115.987Z" />
      <Path fill={colors.accent} d="M110.387 45.7684C102.869 38.2535 92.6608 34.0198 82.0258 34.0234C71.3943 34.0234 61.1863 38.2535 53.668 45.772L82.0258 74.1306L110.387 45.7684Z" />
    </Svg>
  );
}

function SectionHeading({ children }: { children: string }) {
  // minPresenceAhead: don't leave a heading alone at the bottom of a page
  return <Text style={styles.sectionHeading} minPresenceAhead={40}>{children}</Text>;
}

function MetricCard({ card }: { card: ReportMetricCard }) {
  return (
    <View style={styles.card}>
      <Text style={styles.cardLabel}>{card.label}</Text>
      <Text style={styles.cardValue}>
        {card.value}
        {card.unit && <Text style={styles.cardUnit}> {card.unit}</Text>}
      </Text>
      {card.sub && <Text style={styles.cardSub}>{card.sub}</Text>}
    </View>
  );
}

function Trend({ rows }: { rows: NonNullable<ReportModel["trend"]> }) {
  const max = Math.max(1, ...rows.flatMap((r) => [r.raised, r.closed]));
  const pct = (n: number) => (n ? `${Math.max((n / max) * 100, 2)}%` : "0%");
  const series = (n: number, color: string) => (
    <View style={styles.trendSeries}>
      <View style={styles.trendTrack}>
        <View style={[styles.trendBar, { width: pct(n), backgroundColor: color }]} />
      </View>
      <Text style={styles.trendCount}>{n}</Text>
    </View>
  );
  return (
    <View style={styles.trendBox} wrap={false}>
      <View style={styles.trendHeaderRow}>
        <Text style={[styles.trendHeaderCell, styles.trendHeaderMonth]}>Month</Text>
        <Text style={[styles.trendHeaderCell, { flex: 1, color: colors.accent }]}>Raised</Text>
        <Text style={[styles.trendHeaderCell, { flex: 1, color: colors.closed }]}>Closed</Text>
      </View>
      {rows.map((r) => (
        <View key={r.month} style={styles.trendRow}>
          <Text style={styles.trendMonth}>{r.month}</Text>
          {series(r.raised, colors.accent)}
          {series(r.closed, colors.closed)}
        </View>
      ))}
    </View>
  );
}

function Breakdowns({ boxes }: { boxes: NonNullable<ReportModel["breakdowns"]> }) {
  return (
    <View style={styles.breakdownRow} wrap={false}>
      {boxes.map((box) => (
        <View key={box.title} style={styles.breakdownBox}>
          <Text style={styles.breakdownTitle}>{box.title}</Text>
          {box.rows.map((row) => (
            <View key={row.label} style={styles.breakdownItem}>
              <Text style={styles.breakdownLabel}>{row.label}</Text>
              <Text style={styles.breakdownCount}>{row.count}</Text>
              <View style={styles.breakdownTrack}>
                <View style={[styles.breakdownBar, { width: `${row.pct}%` }]} />
              </View>
            </View>
          ))}
        </View>
      ))}
    </View>
  );
}

/** The account summary is short markdown: paragraphs, "- " bullets and **bold**. */
function parseSummary(markdown: string): { bullet: boolean; runs: { text: string; bold: boolean }[] }[] {
  return markdown
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const bullet = /^([-*]|\d+\.)\s+/.test(line);
      const text = line.replace(/^([-*]|\d+\.)\s+/, "");
      const runs = text
        .split(/(\*\*[^*]+\*\*)/)
        .filter(Boolean)
        .map((part) =>
          part.startsWith("**") && part.endsWith("**")
            ? { text: part.slice(2, -2), bold: true }
            : { text: part, bold: false },
        );
      return { bullet, runs };
    });
}

function Summary({ markdown }: { markdown: string }) {
  const blocks = parseSummary(markdown);
  return (
    <View style={styles.summaryBox}>
      {blocks.map((block, i) => {
        const runs = block.runs.map((r, j) => (
          <Text key={j} style={r.bold ? styles.bold : {}}>{r.text}</Text>
        ));
        const spacing = { marginTop: i === 0 ? 0 : block.bullet ? 2 : 7 };
        return block.bullet ? (
          <View key={i} style={[styles.summaryBullet, spacing]}>
            <Text style={{ width: 10 }}>•</Text>
            <Text style={{ flex: 1 }}>{runs}</Text>
          </View>
        ) : (
          <Text key={i} style={[styles.summaryParagraph, spacing]}>{runs}</Text>
        );
      })}
    </View>
  );
}

/** Call registerReportFonts first (renderReportPdf does). */
export function SupportReportPdfDocument({ model }: { model: ReportModel }) {
  const title = `${model.account_name} · Support Highlights`;
  return (
    <Document title={title} author="LangChain" creator="LangChain Support Highlights">
      <Page size="A4" style={styles.page}>
        {model.banner && <Image src={model.banner} style={styles.banner} />}

        <View style={styles.header}>
          <View>
            <View style={styles.brandRow}>
              <Logo />
              <Text style={styles.brandLabel}>Support Highlights</Text>
            </View>
            <Text style={styles.accountName}>{model.account_name}</Text>
          </View>
          <View>
            <Text style={styles.headerMeta}>{model.period_label}</Text>
            <Text style={styles.headerMeta}>Generated {model.generated}</Text>
          </View>
        </View>

        {model.metrics && (
          <View wrap={false}>
            <SectionHeading>Key Metrics</SectionHeading>
            {[model.metrics.top, model.metrics.bottom].filter((row) => row.length).map((row, i) => (
              <View key={i} style={styles.cardRow}>
                {row.map((card) => <MetricCard key={card.label} card={card} />)}
              </View>
            ))}
          </View>
        )}

        {model.trend && (
          <View wrap={false}>
            <SectionHeading>Ticket Trend</SectionHeading>
            <Trend rows={model.trend} />
          </View>
        )}

        {model.breakdowns && model.breakdowns.length > 0 && (
          <View wrap={false}>
            <SectionHeading>Breakdowns</SectionHeading>
            <Breakdowns boxes={model.breakdowns} />
          </View>
        )}

        {model.summary && (
          <View>
            <SectionHeading>Account Summary</SectionHeading>
            <Summary markdown={model.summary} />
          </View>
        )}

        {model.tickets && (
          <View>
            <SectionHeading>{`Open Issues (${model.open_issue_count ?? 0})`}</SectionHeading>
            <SupportReportPdfTickets entries={model.tickets} />
          </View>
        )}

        <Text style={styles.copyright} wrap={false}>
          Copyright © {new Date().getFullYear()} LangChain. All rights reserved.
        </Text>

        <Text style={styles.pageFooterLeft} fixed>{title}</Text>
        <Text
          style={styles.pageFooterRight}
          fixed
          render={({ pageNumber, totalPages }) => `Page ${pageNumber} of ${totalPages}`}
        />
      </Page>
    </Document>
  );
}

/** Render the report to a PDF blob (fonts served from `fontBase`, see registerReportFonts). */
export async function renderReportPdf(model: ReportModel, fontBase: string): Promise<Blob> {
  registerReportFonts(fontBase);
  return pdf(<SupportReportPdfDocument model={model} />).toBlob();
}
