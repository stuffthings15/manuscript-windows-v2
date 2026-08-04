import {
  isSameOriginBrowserRequest,
  readFormDataBody,
  RequestBodyTooLargeError,
} from "../../../lib/request-security";

export async function POST(request: Request) {
  if (!isSameOriginBrowserRequest(request)) {
    return Response.json({ error: "Request rejected." }, { status: 403 });
  }
  let form: FormData;
  try {
    form = await readFormDataBody(request, 4096);
  } catch (error) {
    const status = error instanceof RequestBodyTooLargeError ? 413 : 400;
    return Response.json({ error: "Request rejected." }, { status });
  }
  try {
    const licenseKey = form.get("licenseKey");
    if (typeof licenseKey !== "string") throw new Error("License required.");
    if (!licenseKey.trim()) throw new Error("License required.");
    return Response.redirect("https://www.paypal.com/myaccount/autopay/", 303);
  } catch {
    return Response.json({ error: "Billing portal is unavailable." }, { status: 503 });
  }
}
