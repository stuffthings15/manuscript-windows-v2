# Code Signing Setup (one-time)

Goal: users install Manuscript and the fast local AI engine runs with zero
security popups. Everything below is a one-time setup on the build side;
nothing is ever asked of the user.

## Option A - Azure Trusted Signing (recommended, ~$9.99/month)

1. In the Azure portal create a **Trusted Signing** account (region: East US
   or West Europe host the service).
2. Complete **identity validation** (individual or organization; needs legal
   name and tax/registration details; takes 1-3 business days).
3. Create a **certificate profile** of type *Public Trust*.
4. Install the signing client on the build machine:
   - `dotnet tool install --global azuresigntool` is NOT the path used here;
     install the **Trusted Signing dlib package** (`Microsoft.Trusted.Signing.Client`
     from NuGet) and note the full path of `Azure.CodeSigning.Dlib.dll`.
   - Install **SignTool** via the Windows 11 SDK if not already present.
5. Create `metadata.json` next to your build tools:

    ```json
    {
      "Endpoint": "https://eus.codesigning.azure.net",
      "CodeSigningAccountName": "<account-name>",
      "CertificateProfileName": "<profile-name>"
    }
    ```

6. Authenticate the build shell (`az login`, or a service principal via
   AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET).
7. Set the environment for the build:

    ```powershell
    $env:MANUSCRIPT_AZURE_SIGNING_METADATA = "C:\path\to\metadata.json"
    $env:MANUSCRIPT_AZURE_SIGNING_DLIB = "C:\path\to\Azure.CodeSigning.Dlib.dll"
    python tools\build.py --clean --installer
    ```

The build then signs, in order: the llama.cpp Polish host and its runtime
files, every binary in `dist/Manuscript`, and the installer, each with an
RFC 3161 timestamp (`timestamp.acs.microsoft.com`), and verifies every
signature before finishing.

## Option B - classic OV certificate (thumbprint mode)

Import the certificate into the current-user store, then:

```powershell
$env:MANUSCRIPT_SIGNING_THUMBPRINT = "<40-hex-sha1-thumbprint>"
python tools\build.py --clean --installer
```

## Acceptance checklist (run once after the first signed build)

- Clean Windows 11 VM with **Smart App Control ON**.
- Install `ManuscriptSetup.exe`; expect no SmartScreen or SAC prompt beyond
  the normal elevation consent.
- First correction: Settings > Diagnostics must report the active Polish
  backend as the bundled llama.cpp host, not the Ollama rollback.
- Repeat on a standard (non-admin) user account.

## Why this removes the popups

Smart App Control and SmartScreen trust reputation-verified signatures.
Azure Trusted Signing certificates are Microsoft-issued public-trust
certificates, so signed binaries clear both without any per-machine
configuration. Self-signed certificates do not clear either and are
deliberately not supported by this pipeline.
