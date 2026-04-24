import { NextRequest, NextResponse } from "next/server";

// LOCAL_TEST_MODE bypasses login. Ignored if ALLOWED_ORIGINS is set (production).
const LOCAL_TEST_MODE =
  process.env.LOCAL_TEST_MODE === "true" && !process.env.ALLOWED_ORIGINS;

export function middleware(req: NextRequest) {
  if (LOCAL_TEST_MODE) return NextResponse.next();

  const { pathname } = req.nextUrl;

  // API routes are handled by FastAPI which returns 401 JSON on auth failure.
  // Redirecting them here would send HTML to fetch() callers, breaking JSON parsing.
  if (
    pathname === "/login" ||
    pathname === "/auth/google/callback" ||
    pathname.startsWith("/api/") ||
    pathname.startsWith("/_next/")
  ) {
    return NextResponse.next();
  }

  const session = req.cookies.get("psh_session");
  if (!session?.value) {
    const returnPath = `${req.nextUrl.pathname}${req.nextUrl.search}`;
    const loginUrl = new URL("/login", req.url);
    loginUrl.searchParams.set("return", returnPath);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
