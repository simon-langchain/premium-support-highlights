/** Returns the correct abbreviation for a timezone given its current UTC offset. */
export function getTzAbbr(tz: string, offset: string): string {
  switch (tz) {
    // North America
    case "Pacific/Honolulu":                        return "HST";
    case "America/Anchorage":                       return offset === "GMT-8" ? "AKST" : "AKDT";
    case "America/Los_Angeles":                     return offset === "GMT-8" ? "PST" : "PDT";
    case "America/Denver":                          return offset === "GMT-7" ? "MST" : "MDT";
    case "America/Phoenix":                         return "MST";
    case "America/Chicago":                         return offset === "GMT-6" ? "CST" : "CDT";
    case "America/New_York":                        return offset === "GMT-5" ? "EST" : "EDT";
    case "America/Halifax":                         return offset === "GMT-4" ? "AST" : "ADT";
    case "America/Caracas":                         return "VET";
    case "America/Sao_Paulo":                       return offset === "GMT-3" ? "BRT" : "BRST";
    case "America/Argentina/Buenos_Aires":          return "ART";
    // Europe
    case "Europe/London":                           return offset === "GMT+1" ? "BST" : "GMT";
    case "Europe/Paris":
    case "Europe/Berlin":                           return offset === "GMT+2" ? "CEST" : "CET";
    case "Europe/Helsinki":
    case "Europe/Kyiv":                             return offset === "GMT+3" ? "EEST" : "EET";
    case "Europe/Moscow":                           return "MSK";
    // Africa / Middle East
    case "Africa/Lagos":                            return "WAT";
    case "Africa/Johannesburg":                     return "SAST";
    case "Africa/Cairo":                            return offset === "GMT+3" ? "EEST" : "EET";
    case "Africa/Nairobi":                          return "EAT";
    case "Asia/Riyadh":                             return "AST";
    case "Asia/Tehran":                             return offset === "GMT+4:30" ? "IRDT" : "IRST";
    // Asia
    case "Asia/Dubai":                              return "GST";
    case "Asia/Karachi":                            return "PKT";
    case "Asia/Kolkata":                            return "IST";
    case "Asia/Kathmandu":                          return "NPT";
    case "Asia/Dhaka":                              return "BST";
    case "Asia/Yangon":                             return "MMT";
    case "Asia/Bangkok":                            return "ICT";
    case "Asia/Jakarta":                            return "WIB";
    case "Asia/Singapore":                          return "SGT";
    case "Asia/Hong_Kong":                          return "HKT";
    case "Asia/Shanghai":                           return "CST";
    case "Asia/Seoul":                              return "KST";
    case "Asia/Tokyo":                              return "JST";
    // Australia / Pacific
    case "Australia/Perth":                         return "AWST";
    case "Australia/Darwin":                        return "ACST";
    case "Australia/Adelaide":                      return offset === "GMT+10:30" ? "ACDT" : "ACST";
    case "Australia/Brisbane":                      return "AEST";
    case "Australia/Sydney":                        return offset === "GMT+11" ? "AEDT" : "AEST";
    case "Pacific/Auckland":                        return offset === "GMT+13" ? "NZDT" : "NZST";
    case "Pacific/Fiji":                            return "FJT";
    default:                                        return "";
  }
}

/** Returns the short timezone abbreviation for compact display (e.g. "PDT", "BST", "IST"). */
export function tzShort(tz: string): string {
  if (tz === "UTC") return "UTC";
  try {
    const offset = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "shortOffset" })
      .formatToParts(new Date()).find((p) => p.type === "timeZoneName")?.value ?? "";
    return getTzAbbr(tz, offset) || offset;
  } catch {
    return tz;
  }
}

