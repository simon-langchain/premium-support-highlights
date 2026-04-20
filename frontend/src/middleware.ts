import { NextRequest, NextResponse } from "next/server";

// LOCAL_TEST_MODE bypasses login. Ignored if ALLOWED_ORIGINS is set (production).
const LOCAL_TEST_MODE =
  process.env.LOCAL_TEST_MODE === "true" && !process.env.ALLOWED_ORIGINS;

export function middleware(req: NextRequest) {
  if (LOCAL_TEST_MODE) return NextResponse.next();

  const { pathname } = req.nextUrl;

  // Auth routes, login page, and Slack callbacks are always accessible
  if (
    pathname === "/login" ||
    pathname === "/auth/google/callback" ||
    pathname.startsWith("/api/auth/") ||
    pathname.startsWith("/api/slack/") ||
    pathname.startsWith("/_next/")
  ) {
    return NextResponse.next();
  }

  const session = req.cookies.get("psh_session");
  if (!session?.value) {
    return NextResponse.redirect(new URL("/login", req.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
