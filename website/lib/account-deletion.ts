import { deleteIdentityAccount } from "./account-identity";
import {
  finalizeAccountDeletion,
  markAccountDeletionFailure,
  pendingAccountDeletionJobs,
} from "./entitlements";
import { requireSecret } from "./runtime";

export async function processPendingAccountDeletionJobs(
  limit = 10,
): Promise<void> {
  const jobs = await pendingAccountDeletionJobs(limit);
  for (const job of jobs) {
    try {
      await deleteIdentityAccount(
        job.accountId,
        requireSecret("IDENTITY_PROVIDER_ADMIN_KEY"),
        {
          origin: requireSecret("IDENTITY_PROVIDER_URL"),
          publicKey: requireSecret("IDENTITY_PROVIDER_PUBLIC_KEY"),
        },
      );
      await finalizeAccountDeletion(job.accountId, job.accountEmail);
    } catch {
      await markAccountDeletionFailure(job.accountId);
    }
  }
}
