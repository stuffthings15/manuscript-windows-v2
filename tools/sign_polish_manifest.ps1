param(
    [string]$ManifestPath = "",
    [string]$PrivateKeyPath = "",
    [string]$SignaturePath = "",
    [string]$PublicKeyPath = "",
    [switch]$Rotate
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
$root = Split-Path -Parent $PSScriptRoot
if (-not $ManifestPath) {
    $ManifestPath = Join-Path $root "resources\polish\model-packs.json"
}
if (-not $PrivateKeyPath) {
    $local = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    $PrivateKeyPath = Join-Path $local "Echo-Link\release-keys\polish-manifest-private.dpapi"
}
if (-not $SignaturePath) {
    $manifestName = [IO.Path]::GetFileNameWithoutExtension($ManifestPath)
    $SignaturePath = Join-Path (Split-Path -Parent $ManifestPath) ($manifestName + ".sig")
}
if (-not $PublicKeyPath) {
    $PublicKeyPath = Join-Path (Split-Path -Parent $ManifestPath) "model-packs-public-key.json"
}

$manifest = [IO.File]::ReadAllBytes([IO.Path]::GetFullPath($ManifestPath))
$privatePath = [IO.Path]::GetFullPath($PrivateKeyPath)
$privateDirectory = Split-Path -Parent $privatePath
[IO.Directory]::CreateDirectory($privateDirectory) | Out-Null
$entropy = [Text.Encoding]::UTF8.GetBytes("Manuscript Polish Manifest Key v1")

if ($Rotate -and [IO.File]::Exists($privatePath)) {
    throw "Key rotation requires moving the old private key to an approved archive first."
}

$rsa = [Security.Cryptography.RSACryptoServiceProvider]::new(3072)
$rsa.PersistKeyInCsp = $false
try {
    if ([IO.File]::Exists($privatePath)) {
        $wrapped = [IO.File]::ReadAllBytes($privatePath)
        $private = [Security.Cryptography.ProtectedData]::Unprotect(
            $wrapped,
            $entropy,
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        $privateXml = [Text.Encoding]::UTF8.GetString($private)
        $rsa.FromXmlString($privateXml)
        [Array]::Clear($private, 0, $private.Length)
    }
    else {
        $private = [Text.Encoding]::UTF8.GetBytes($rsa.ToXmlString($true))
        $wrapped = [Security.Cryptography.ProtectedData]::Protect(
            $private,
            $entropy,
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        [IO.File]::WriteAllBytes($privatePath, $wrapped)
        [Array]::Clear($private, 0, $private.Length)
    }

    $parameters = $rsa.ExportParameters($false)
    $exponent = 0
    foreach ($value in $parameters.Exponent) {
        $exponent = ($exponent * 256) + [int]$value
    }
    $public = [ordered]@{
        key_id = "manuscript-polish-2026-01"
        algorithm = "RSA-3072-PKCS1-SHA256"
        modulus_base64 = [Convert]::ToBase64String($parameters.Modulus)
        exponent = $exponent
    }
    $publicPath = [IO.Path]::GetFullPath($PublicKeyPath)
    $publicJson = $public | ConvertTo-Json
    [IO.File]::WriteAllText($publicPath, $publicJson + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))

    $signature = $rsa.SignData(
        $manifest,
        [Security.Cryptography.CryptoConfig]::MapNameToOID("SHA256")
    )
    $signaturePath = [IO.Path]::GetFullPath($SignaturePath)
    [IO.File]::WriteAllText(
        $signaturePath,
        [Convert]::ToBase64String($signature) + [Environment]::NewLine,
        [Text.Encoding]::ASCII
    )

    $canonical = (
        '{"exponent":' + $exponent +
        ',"key_id":"manuscript-polish-2026-01","modulus_base64":"' +
        $public.modulus_base64 + '"}'
    )
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $fingerprintBytes = $sha.ComputeHash(
            [Text.Encoding]::UTF8.GetBytes($canonical)
        )
        $fingerprint = -join (
            $fingerprintBytes | ForEach-Object { $_.ToString("x2") }
        )
    }
    finally {
        $sha.Dispose()
    }
    [pscustomobject]@{
        manifest = [IO.Path]::GetFullPath($ManifestPath)
        signature = [IO.Path]::GetFullPath($signaturePath)
        public_key = [IO.Path]::GetFullPath($publicPath)
        private_key = $privatePath
        public_key_fingerprint = $fingerprint
    } | ConvertTo-Json
}
finally {
    $rsa.Dispose()
}
