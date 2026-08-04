import { accountFromAccessToken } from "../../../../lib/account-identity";
import {
  accessTokenFromRequest,
  platformEntitlementResponse,
  platformFromRequest,
} from "../../../../lib/account-session";
import { accountEntitlement } from "../../../../lib/entitlements";
import { identityProviderConfig } from "../../../account-auth";

export async function GET(request: Request) {
  try {
    const token = accessTokenFromRequest(request);
    if (!token) {
      return Response.json(
        { error: "Authentication required." },
        { status: 401, headers: { "Cache-Control": "no-store" } },
      );
    }
    const account = await accountFromAccessToken(
      token,
      identityProviderConfig(),
    );
    const entitlement = await accountEntitlement(account.id);
    return Response.json(
      platformEntitlementResponse(
        account,
        entitlement,
        platformFromRequest(request),
      ),
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return Response.json(
      { error: "Account access could not be verified." },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }
}
