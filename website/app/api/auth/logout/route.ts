import { clearSessionCookieHeaders } from "../../../../lib/account-session";
import { isSameOriginBrowserRequest } from "../../../../lib/request-security";

export async function POST(request: Request) {
  if (!isSameOriginBrowserRequest(request)) {
    return Response.json({ error: "Invalid request origin." }, { status: 403 });
  }
  const headers = new Headers({
    Location: "/",
    "Cache-Control": "no-store",
  });
  for (const cookie of clearSessionCookieHeaders()) headers.append("Set-Cookie", cookie);
  return new Response(null, { status: 303, headers });
}
