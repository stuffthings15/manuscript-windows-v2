using System.Buffers.Binary;
using System.Diagnostics;
using System.IO.Pipes;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Manuscript.PolishHost;

internal static partial class Program
{
    private const int ProtocolVersion = 1;
    private const int MaxMessageBytes = 8 * 1024 * 1024;
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    public static async Task<int> Main(string[] args)
    {
        // Print-only, for tests of the built executable: it opens no pipe,
        // starts no model, and reads nothing but these two arguments. A test
        // that reads this source file would pass while an old executable sat
        // in resources/, so the binary has to be able to answer for itself.
        if (args.Length == 2 && args[0] == "--print-system-prompt"
            && (args[1] == "faithful" || args[1] == "free"))
        {
            Console.Out.Write(LlamaServerRuntime.SystemPrompt(args[1] == "faithful"));
            return 0;
        }
        // Test-only, for the built executable: start a harmless stand-in
        // child through the same launch helper llama-server uses, report its
        // PID, and wait to be killed. The test kills this host with
        // taskkill /F and no /T, and checks the child went with it.
        if (args.Length == 1 && args[0] == "--self-test-child-job")
        {
            var start = new ProcessStartInfo("cmd.exe")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            foreach (string value in new[] { "/c", "ping", "-n", "120", "127.0.0.1" })
            {
                start.ArgumentList.Add(value);
            }
            start.RedirectStandardOutput = true;
            using Process child = LlamaServerRuntime.StartBoundToHost(start);
            Console.Out.WriteLine($"child-pid {child.Id}");
            Console.Out.Flush();
            await Task.Delay(TimeSpan.FromSeconds(120));
            return 0;
        }
        try
        {
            HostConfiguration configuration = HostConfiguration.Parse(args);
            using var lifetime = new CancellationTokenSource();
            await using var inference = new LlamaServerRuntime(configuration);
            _ = MonitorParentAsync(configuration.ParentProcessId, lifetime);
            await RunPipeServerAsync(configuration, inference, lifetime);
            return 0;
        }
        catch (Exception error) when (
            error is ArgumentException
            or IOException
            or InvalidOperationException
            or JsonException
            or UnauthorizedAccessException)
        {
            Console.Error.WriteLine(SafeError(error));
            return 1;
        }
    }

    private static async Task RunPipeServerAsync(
        HostConfiguration configuration,
        LlamaServerRuntime inference,
        CancellationTokenSource lifetime)
    {
        var connections = new List<Task>();
        while (!lifetime.IsCancellationRequested)
        {
            var pipe = new NamedPipeServerStream(
                configuration.PipeName,
                PipeDirection.InOut,
                4,
                PipeTransmissionMode.Byte,
                PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly,
                64 * 1024,
                64 * 1024);
            try
            {
                await pipe.WaitForConnectionAsync(lifetime.Token);
                connections.RemoveAll(value => value.IsCompleted);
                connections.Add(
                    HandleConnectionAsync(
                        pipe,
                        configuration,
                        inference,
                        lifetime));
            }
            catch (OperationCanceledException) when (lifetime.IsCancellationRequested)
            {
                await pipe.DisposeAsync();
                break;
            }
        }
        await Task.WhenAll(connections);
    }

    private static async Task HandleConnectionAsync(
        NamedPipeServerStream pipe,
        HostConfiguration configuration,
        LlamaServerRuntime inference,
        CancellationTokenSource lifetime)
    {
        await using (pipe)
        {
            Dictionary<string, object?> response;
            try
            {
                byte[] requestBytes = await ReadFrameAsync(pipe, lifetime.Token);
                using JsonDocument document = JsonDocument.Parse(requestBytes);
                JsonElement request = document.RootElement;
                Authenticate(request, configuration.Secret);
                response = await DispatchAsync(
                    request,
                    configuration,
                    inference,
                    lifetime);
            }
            catch (Exception error) when (
                error is ArgumentException
                or IOException
                or HttpRequestException
                or InvalidOperationException
                or JsonException
                or TaskCanceledException
                or UnauthorizedAccessException)
            {
                response = new()
                {
                    ["ok"] = false,
                    ["error"] = SafeError(error),
                };
            }
            await WriteFrameAsync(
                pipe,
                JsonSerializer.SerializeToUtf8Bytes(response, JsonOptions),
                lifetime.Token);
            await pipe.FlushAsync(lifetime.Token);
        }
    }

    private static async Task<Dictionary<string, object?>> DispatchAsync(
        JsonElement request,
        HostConfiguration configuration,
        LlamaServerRuntime inference,
        CancellationTokenSource lifetime)
    {
        string type = RequiredString(request, "type", 40);
        if (type == "hello")
        {
            return new()
            {
                ["ok"] = true,
                ["protocol_version"] = ProtocolVersion,
                ["runtime"] = "llama.cpp",
                ["host_version"] = "2.1.0",
                ["model_pack_id"] = configuration.ModelPackId,
                ["model"] = configuration.ModelName,
                ["backend"] = configuration.Backend,
                ["context_window"] = configuration.ContextWindow,
            };
        }
        if (type == "shutdown")
        {
            lifetime.CancelAfter(TimeSpan.FromMilliseconds(100));
            return new() { ["ok"] = true };
        }
        if (type == "status")
        {
            RequirePlanIdentity(request, configuration, requirePack: false);
            RuntimeState state = await inference.StatusAsync(
                TimeSpan.FromSeconds(BoundedDouble(request, "timeout_seconds", 1, 0.2, 5)));
            return StatusResponse(state);
        }
        if (type == "generate")
        {
            RequirePlanIdentity(request, configuration, requirePack: true);
            GenerationResponse generated = await GenerateAsync(request, inference);
            RuntimeState state = await inference.StatusAsync(TimeSpan.FromSeconds(2));
            if (configuration.Backend != "cpu" && !state.FullGpuResident)
            {
                throw new InvalidOperationException(
                    "llama.cpp did not retain full GPU residency. CPU/GPU spill was rejected.");
            }
            return new()
            {
                ["ok"] = true,
                ["text"] = generated.Text,
                ["model"] = configuration.ModelName,
                ["backend"] = configuration.Backend,
                ["full_gpu_resident"] = state.FullGpuResident,
                ["gpu_bytes"] = state.GpuBytes,
                ["input_tokens"] = generated.InputTokens,
                ["output_tokens"] = generated.OutputTokens,
                ["elapsed_seconds"] = generated.ElapsedSeconds,
            };
        }
        if (type == "acceptance_probe")
        {
            RequirePlanIdentity(request, configuration, requirePack: true);
            return await AcceptanceProbeAsync(request, configuration, inference);
        }
        throw new ArgumentException("Unknown Polish host request type.");
    }

    private static async Task<GenerationResponse> GenerateAsync(
        JsonElement request,
        LlamaServerRuntime inference)
    {
        string text = RequiredString(request, "text", 2_000_000);
        string command = OptionalString(request, "command", 80);
        string profile = OptionalString(request, "profile", 80);
        string instruction = OptionalString(request, "instruction", 16_000);
        string language = OptionalString(request, "language", 120);
        int outputTokens = BoundedInt(request, "max_output_tokens", 512, 1, 4096);
        double timeout = BoundedDouble(request, "timeout_seconds", 30, 1, 30);
        // The writer's "Keep my meaning exactly" switch. Absent means ON, so
        // an older client keeps the behaviour it was built against.
        bool faithful = OptionalBool(request, "faithful", true);
        var protectedTerms = new List<string>();
        if (
            request.TryGetProperty("protected_terms", out JsonElement rawTerms)
            && rawTerms.ValueKind == JsonValueKind.Array)
        {
            foreach (JsonElement value in rawTerms.EnumerateArray().Take(2000))
            {
                if (value.ValueKind == JsonValueKind.String)
                {
                    string term = value.GetString() ?? "";
                    if (term.Length is > 0 and <= 500)
                    {
                        protectedTerms.Add(term);
                    }
                }
            }
        }
        return await inference.GenerateAsync(
            text,
            command,
            profile,
            instruction,
            language,
            protectedTerms,
            faithful,
            outputTokens,
            TimeSpan.FromSeconds(timeout));
    }

    private static async Task<Dictionary<string, object?>> AcceptanceProbeAsync(
        JsonElement request,
        HostConfiguration configuration,
        LlamaServerRuntime inference)
    {
        double limit = BoundedDouble(
            request,
            "latency_limit_seconds",
            15,
            0.1,
            30);
        const string source =
            "Every day, the report are not ready, and it fail. Keep build 47 at " +
            "http://127.0.0.1:7777/protected until July 23, 2026.";
        try
        {
            await inference.WarmAsync(TimeSpan.FromSeconds(30));
            var timer = Stopwatch.StartNew();
            GenerationResponse result = await inference.GenerateAsync(
                source,
                "Proofread",
                "General",
                "Correct every grammar error. Keep the date, build number, URL, " +
                "and negative statement exact.",
                "",
                [],
                // The acceptance probe proves the faithful contract: the
                // date, build number, URL and negation must survive.
                true,
                128,
                TimeSpan.FromSeconds(30));
            string folded = Whitespace().Replace(result.Text.ToLowerInvariant(), " ").Trim();
            bool correctness =
                folded.Contains("report is not ready", StringComparison.Ordinal)
                && folded.Contains("it fails", StringComparison.Ordinal)
                && folded.Contains("build 47", StringComparison.Ordinal);
            bool protectedPassed =
                result.Text.Contains("July 23, 2026", StringComparison.Ordinal)
                && result.Text.Contains("47", StringComparison.Ordinal)
                && result.Text.Contains(
                    "http://127.0.0.1:7777/protected",
                    StringComparison.Ordinal);
            RuntimeState state = await inference.StatusAsync(TimeSpan.FromSeconds(2));
            bool backendPassed =
                configuration.Backend == "cpu" || state.FullGpuResident;
            bool latencyPassed = timer.Elapsed.TotalSeconds <= limit;
            var failures = new List<string>();
            if (!correctness) failures.Add("correctness probe failed");
            if (!protectedPassed) failures.Add("protected facts changed");
            if (!backendPassed) failures.Add("full GPU residency failed");
            if (!latencyPassed)
            {
                failures.Add(
                    $"warm probe took {timer.Elapsed.TotalSeconds:F2}s; limit is {limit:F2}s");
            }
            return new()
            {
                ["ok"] = true,
                ["accepted"] =
                    correctness && protectedPassed && backendPassed && latencyPassed,
                ["elapsed_seconds"] = timer.Elapsed.TotalSeconds,
                ["correctness_passed"] = correctness,
                ["protected_passed"] = protectedPassed,
                ["model"] = configuration.ModelName,
                ["backend"] = configuration.Backend,
                ["full_gpu_resident"] = state.FullGpuResident,
                ["reason"] = string.Join("; ", failures),
            };
        }
        catch (Exception error) when (
            error is IOException
            or HttpRequestException
            or InvalidOperationException
            or TaskCanceledException)
        {
            return new()
            {
                ["ok"] = true,
                ["accepted"] = false,
                ["elapsed_seconds"] = 0.0,
                ["correctness_passed"] = false,
                ["protected_passed"] = false,
                ["model"] = configuration.ModelName,
                ["backend"] = configuration.Backend,
                ["full_gpu_resident"] = false,
                ["reason"] = SafeError(error),
            };
        }
    }

    private static Dictionary<string, object?> StatusResponse(RuntimeState state) => new()
    {
        ["ok"] = true,
        ["model"] = state.Model,
        ["loaded"] = state.Loaded,
        ["backend"] = state.Backend,
        ["model_bytes"] = state.ModelBytes,
        ["gpu_bytes"] = state.GpuBytes,
        ["detail"] = state.Detail,
        ["full_gpu_resident"] = state.FullGpuResident,
        ["load_elapsed_seconds"] = state.LoadElapsedSeconds,
    };

    private static void RequirePlanIdentity(
        JsonElement request,
        HostConfiguration configuration,
        bool requirePack)
    {
        string backend = RequiredString(request, "backend", 20);
        if (!backend.Equals(configuration.Backend, StringComparison.Ordinal))
        {
            throw new UnauthorizedAccessException("Polish host backend identity mismatch.");
        }
        int context = BoundedInt(request, "context_window", 0, 512, 32768);
        if (context != configuration.ContextWindow)
        {
            throw new UnauthorizedAccessException("Polish host context identity mismatch.");
        }
        string model = RequiredString(request, "model", 120);
        if (!model.Equals(configuration.ModelName, StringComparison.Ordinal))
        {
            throw new UnauthorizedAccessException("Polish host model identity mismatch.");
        }
        if (requirePack)
        {
            string pack = RequiredString(request, "model_pack_id", 120);
            if (!pack.Equals(configuration.ModelPackId, StringComparison.Ordinal))
            {
                throw new UnauthorizedAccessException("Polish host pack identity mismatch.");
            }
        }
    }

    private static void Authenticate(JsonElement request, string expectedSecret)
    {
        if (
            !request.TryGetProperty("protocol_version", out JsonElement protocol)
            || protocol.ValueKind != JsonValueKind.Number
            || protocol.GetInt32() != ProtocolVersion)
        {
            throw new UnauthorizedAccessException("Polish host protocol mismatch.");
        }
        string supplied = RequiredString(request, "auth", 128);
        byte[] left = Encoding.UTF8.GetBytes(supplied);
        byte[] right = Encoding.UTF8.GetBytes(expectedSecret);
        bool matches = left.Length == right.Length
            && CryptographicOperations.FixedTimeEquals(left, right);
        CryptographicOperations.ZeroMemory(left);
        CryptographicOperations.ZeroMemory(right);
        if (!matches)
        {
            throw new UnauthorizedAccessException("Polish host authentication failed.");
        }
    }

    private static async Task<byte[]> ReadFrameAsync(Stream stream, CancellationToken token)
    {
        byte[] lengthBytes = new byte[4];
        await ReadExactlyAsync(stream, lengthBytes, token);
        int length = BinaryPrimitives.ReadInt32LittleEndian(lengthBytes);
        if (length is <= 0 or > MaxMessageBytes)
        {
            throw new InvalidOperationException("Polish host request size is invalid.");
        }
        byte[] payload = new byte[length];
        await ReadExactlyAsync(stream, payload, token);
        return payload;
    }

    private static async Task WriteFrameAsync(
        Stream stream,
        byte[] payload,
        CancellationToken token)
    {
        if (payload.Length is <= 0 or > MaxMessageBytes)
        {
            throw new InvalidOperationException("Polish host response size is invalid.");
        }
        byte[] lengthBytes = new byte[4];
        BinaryPrimitives.WriteInt32LittleEndian(lengthBytes, payload.Length);
        await stream.WriteAsync(lengthBytes, token);
        await stream.WriteAsync(payload, token);
    }

    private static async Task ReadExactlyAsync(
        Stream stream,
        byte[] buffer,
        CancellationToken token)
    {
        int offset = 0;
        while (offset < buffer.Length)
        {
            int read = await stream.ReadAsync(buffer.AsMemory(offset), token);
            if (read == 0)
            {
                throw new IOException("Polish host pipe closed early.");
            }
            offset += read;
        }
    }

    private static async Task MonitorParentAsync(
        int processId,
        CancellationTokenSource lifetime)
    {
        while (!lifetime.IsCancellationRequested)
        {
            try
            {
                using Process parent = Process.GetProcessById(processId);
                if (parent.HasExited)
                {
                    lifetime.Cancel();
                    return;
                }
            }
            catch (ArgumentException)
            {
                lifetime.Cancel();
                return;
            }
            try
            {
                await Task.Delay(TimeSpan.FromSeconds(2), lifetime.Token);
            }
            catch (OperationCanceledException)
            {
                return;
            }
        }
    }

    private static string RequiredString(JsonElement value, string name, int maxLength)
    {
        if (
            !value.TryGetProperty(name, out JsonElement raw)
            || raw.ValueKind != JsonValueKind.String)
        {
            throw new ArgumentException($"Polish host field '{name}' is required.");
        }
        string result = raw.GetString() ?? "";
        if (result.Length == 0 || result.Length > maxLength)
        {
            throw new ArgumentException($"Polish host field '{name}' is invalid.");
        }
        return result;
    }

    private static bool OptionalBool(JsonElement value, string name, bool fallback)
    {
        if (!value.TryGetProperty(name, out JsonElement raw))
        {
            return fallback;
        }
        return raw.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            _ => throw new ArgumentException($"Polish host field '{name}' is invalid."),
        };
    }

    private static string OptionalString(JsonElement value, string name, int maxLength)
    {
        if (!value.TryGetProperty(name, out JsonElement raw))
        {
            return "";
        }
        if (raw.ValueKind != JsonValueKind.String)
        {
            throw new ArgumentException($"Polish host field '{name}' is invalid.");
        }
        string result = raw.GetString() ?? "";
        if (result.Length > maxLength)
        {
            throw new ArgumentException($"Polish host field '{name}' is too long.");
        }
        return result;
    }

    private static int BoundedInt(
        JsonElement value,
        string name,
        int fallback,
        int minimum,
        int maximum)
    {
        if (!value.TryGetProperty(name, out JsonElement raw))
        {
            return fallback;
        }
        if (raw.ValueKind != JsonValueKind.Number || !raw.TryGetInt32(out int result))
        {
            throw new ArgumentException($"Polish host field '{name}' is invalid.");
        }
        return Math.Clamp(result, minimum, maximum);
    }

    private static double BoundedDouble(
        JsonElement value,
        string name,
        double fallback,
        double minimum,
        double maximum)
    {
        if (!value.TryGetProperty(name, out JsonElement raw))
        {
            return fallback;
        }
        if (
            raw.ValueKind != JsonValueKind.Number
            || !raw.TryGetDouble(out double result)
            || !double.IsFinite(result))
        {
            throw new ArgumentException($"Polish host field '{name}' is invalid.");
        }
        return Math.Clamp(result, minimum, maximum);
    }

    private static string SafeError(Exception error)
    {
        string value = Whitespace().Replace(error.Message ?? "Local inference failed.", " ");
        value = value.Replace(Environment.UserName, "<user>", StringComparison.OrdinalIgnoreCase);
        return value.Length <= 500 ? value : value[..500];
    }

    [GeneratedRegex(@"\s+")]
    private static partial Regex Whitespace();
}

internal sealed record HostConfiguration(
    string PipeName,
    string Secret,
    string ServerPath,
    string RuntimeRoot,
    string ModelPath,
    string ModelRoot,
    string ModelPackId,
    string ModelName,
    string Backend,
    int ParentProcessId,
    int IdleSeconds,
    int ContextWindow)
{
    private static readonly Regex SafePipe = new(
        @"^ManuscriptPolish-[a-f0-9]{16,64}$",
        RegexOptions.CultureInvariant);

    public static HostConfiguration Parse(string[] args)
    {
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        for (int index = 0; index < args.Length; index += 2)
        {
            if (
                index + 1 >= args.Length
                || !args[index].StartsWith("--", StringComparison.Ordinal))
            {
                throw new ArgumentException("Polish host arguments are invalid.");
            }
            values.Add(args[index][2..], args[index + 1]);
        }
        string pipeName = Required(values, "pipe-name");
        if (!SafePipe.IsMatch(pipeName))
        {
            throw new ArgumentException("Polish host pipe name is invalid.");
        }
        string secret =
            Environment.GetEnvironmentVariable("MANUSCRIPT_POLISH_PIPE_SECRET") ?? "";
        Environment.SetEnvironmentVariable("MANUSCRIPT_POLISH_PIPE_SECRET", null);
        if (
            secret.Length != 64
            || secret.Any(value => !Uri.IsHexDigit(value)))
        {
            throw new UnauthorizedAccessException("Polish host secret is invalid.");
        }
        string runtimeRoot = SafeRoot(Required(values, "runtime-root"));
        string modelRoot = SafeRoot(Required(values, "model-root"));
        string serverPath = SafeChildFile(
            runtimeRoot,
            Required(values, "server"),
            "llama-server.exe");
        string modelPath = SafeChildFile(
            modelRoot,
            Required(values, "model"),
            expectedName: null);
        using (FileStream model = File.OpenRead(modelPath))
        {
            Span<byte> magic = stackalloc byte[4];
            if (model.Read(magic) != magic.Length || !magic.SequenceEqual("GGUF"u8))
            {
                throw new InvalidOperationException("Polish model is not a GGUF file.");
            }
        }
        string backend = Required(values, "backend").ToLowerInvariant();
        if (backend is not ("cpu" or "cuda" or "vulkan" or "hip" or "sycl"))
        {
            throw new ArgumentException("Polish host backend is invalid.");
        }
        if (
            !int.TryParse(Required(values, "parent-pid"), out int parent)
            || parent <= 0)
        {
            throw new ArgumentException("Polish host parent process is invalid.");
        }
        int idle = ParseBounded(values, "idle-seconds", 300, 60, 3600);
        int context = ParseBounded(values, "context-window", 4096, 512, 32768);
        string modelPackId = Required(values, "model-pack-id");
        if (
            modelPackId.Length > 120
            || modelPackId.Any(value => !char.IsLetterOrDigit(value) && value is not ('.' or '_' or '-')))
        {
            throw new ArgumentException("Polish host model pack ID is invalid.");
        }
        string modelName = Required(values, "model-name");
        if (modelName.Length > 120)
        {
            throw new ArgumentException("Polish host model name is invalid.");
        }
        return new(
            pipeName,
            secret.ToLowerInvariant(),
            serverPath,
            runtimeRoot,
            modelPath,
            modelRoot,
            modelPackId,
            modelName,
            backend,
            parent,
            idle,
            context);
    }

    private static string SafeRoot(string value)
    {
        string result = Path.TrimEndingDirectorySeparator(Path.GetFullPath(value));
        if (
            !Path.IsPathFullyQualified(result)
            || result.StartsWith(@"\\", StringComparison.Ordinal)
            || !Directory.Exists(result))
        {
            throw new ArgumentException("Polish host root path is invalid.");
        }
        RejectReparsePoints(result);
        return result;
    }

    private static string SafeChildFile(
        string root,
        string value,
        string? expectedName)
    {
        string result = Path.GetFullPath(value);
        string prefix = root + Path.DirectorySeparatorChar;
        if (
            !result.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)
            || !File.Exists(result)
            || (expectedName is not null
                && !Path.GetFileName(result).Equals(
                    expectedName,
                    StringComparison.OrdinalIgnoreCase)))
        {
            throw new ArgumentException("Polish host file path is invalid.");
        }
        RejectReparsePoints(result);
        return result;
    }

    private static void RejectReparsePoints(string value)
    {
        string root = Path.GetPathRoot(value) ?? "";
        if (root.Length == 0 || root.StartsWith(@"\\", StringComparison.Ordinal))
        {
            throw new ArgumentException("Polish host path is not local.");
        }
        string current = root;
        foreach (
            string component in value[root.Length..].Split(
                [Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar],
                StringSplitOptions.RemoveEmptyEntries))
        {
            current = Path.Combine(current, component);
            if (
                (File.GetAttributes(current) & FileAttributes.ReparsePoint)
                != 0)
            {
                throw new UnauthorizedAccessException(
                    "Polish host path contains a link or junction.");
            }
        }
    }

    private static string Required(Dictionary<string, string> values, string name)
    {
        if (!values.TryGetValue(name, out string? result) || string.IsNullOrWhiteSpace(result))
        {
            throw new ArgumentException($"Polish host argument '--{name}' is required.");
        }
        return result;
    }

    private static int ParseBounded(
        Dictionary<string, string> values,
        string name,
        int fallback,
        int minimum,
        int maximum)
    {
        if (!values.TryGetValue(name, out string? raw))
        {
            return fallback;
        }
        if (!int.TryParse(raw, out int result) || result < minimum || result > maximum)
        {
            throw new ArgumentException($"Polish host argument '--{name}' is invalid.");
        }
        return result;
    }
}

internal sealed class LlamaServerRuntime : IAsyncDisposable
{
    private readonly HostConfiguration _configuration;
    private readonly HttpClient _http = new();
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly List<string> _safeLog = [];
    private Process? _process;
    private Uri? _baseAddress;
    private string _apiKey = "";
    private DateTimeOffset _loadStarted;
    private DateTimeOffset _lastGeneration;
    private long _gpuBytes;
    private bool _fullGpuResident;
    private bool _captureLoadLogs;

    public LlamaServerRuntime(HostConfiguration configuration)
    {
        _configuration = configuration;
    }

    // The faithful prompt is the one this host always sent. The free one is
    // for a writer who has turned "Keep my meaning exactly" off (asked for
    // on 2026-09-30): the client's instruction already says so, but this
    // prompt said "preserve every actor, negation, obligation and factual
    // relationship exactly" and "apply the smallest complete correction" on
    // every request, and a system prompt outranks an instruction. What stays
    // hard either way is what the app's validator still enforces when the
    // switch is off: the protected values, names, numbers, dates, URLs, paths
    // and code, and an answer that is only the text.
    internal static string SystemPrompt(bool faithful)
    {
        if (faithful)
        {
            return
                "You are Manuscript's private local writing editor. Return only the finished " +
                "text. Never add a label, explanation, quote wrapper, fact, name, promise, or " +
                "commentary. Preserve every protected value, number, date, URL, path, code " +
                "span, actor, negation, obligation, ordering word, paragraph, and factual " +
                "relationship exactly. Apply the smallest complete correction. Do not think " +
                "aloud. Thinking is disabled.";
        }
        return
            "You are Manuscript's private local writing editor. Return only the finished " +
            "text. Never add a label, explanation, quote wrapper, or commentary. The writer " +
            "has turned off meaning preservation and wants the text to read sensibly above " +
            "all. Where the source is confused, contradictory, padded, or self-undermining, " +
            "rewrite it so it makes sense: restructure, merge or split sentences, drop " +
            "filler, and repair muddled claims. Keep every protected value, name, number, " +
            "date, URL, path, and code span exact, keep the same paragraphs, and keep the " +
            "text about the same subject. Do not think aloud. Thinking is disabled.";
    }

    public async Task<GenerationResponse> GenerateAsync(
        string text,
        string command,
        string profile,
        string instruction,
        string language,
        IReadOnlyList<string> protectedTerms,
        bool faithful,
        int maxOutputTokens,
        TimeSpan timeout)
    {
        await _gate.WaitAsync(timeout);
        try
        {
            await EnsureReadyAsync(timeout);
            using var request = new HttpRequestMessage(
                HttpMethod.Post,
                new Uri(_baseAddress!, "/v1/chat/completions"));
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _apiKey);
            string protectedBlock = protectedTerms.Count == 0
                ? "None"
                : string.Join("\n", protectedTerms.Select(value => "- " + value));
            string system = SystemPrompt(faithful);
            // "/no_think" is a QWEN control token, and only Qwen strips it.
            // Every other family reads it as ordinary text, so it can be
            // echoed straight into the writer's document. On 2026-09-01, with
            // gemma4:12b-it-qat behind this host, dictation returned
            // "Testing, testing. /no_think." into Notepad. The system prompt
            // above already says thinking is disabled, so nothing is lost by
            // withholding the token from models that do not understand it.
            bool understandsNoThink =
                _configuration.ModelName is not null
                && _configuration.ModelName.Contains(
                    "qwen", StringComparison.OrdinalIgnoreCase);
            string thinkingDirective = understandsNoThink ? "\n/no_think" : string.Empty;
            string user =
                $"ACTION: {command}\nPROFILE: {profile}\nLANGUAGE: " +
                $"{(string.IsNullOrWhiteSpace(language) ? "Same as source" : language)}\n" +
                $"INSTRUCTION: {instruction}\nPROTECTED VALUES:\n{protectedBlock}\n\n" +
                $"TEXT:\n{text}{thinkingDirective}";
            object payload = new
            {
                model = _configuration.ModelName,
                messages = new[]
                {
                    new { role = "system", content = system },
                    new { role = "user", content = user },
                },
                temperature = 0.0,
                top_p = 0.9,
                max_tokens = maxOutputTokens,
                stream = false,
            };
            request.Content = new ByteArrayContent(
                JsonSerializer.SerializeToUtf8Bytes(payload));
            request.Content.Headers.ContentType =
                new MediaTypeHeaderValue("application/json");
            using var deadline = new CancellationTokenSource(timeout);
            var timer = Stopwatch.StartNew();
            using HttpResponseMessage response = await _http.SendAsync(
                request,
                HttpCompletionOption.ResponseHeadersRead,
                deadline.Token);
            byte[] raw = await response.Content.ReadAsByteArrayAsync(deadline.Token);
            if (!response.IsSuccessStatusCode)
            {
                throw new InvalidOperationException(
                    $"llama.cpp generation failed with HTTP {(int)response.StatusCode}.");
            }
            using JsonDocument document = JsonDocument.Parse(raw);
            JsonElement root = document.RootElement;
            if (
                !root.TryGetProperty("model", out JsonElement reportedModel)
                || reportedModel.ValueKind != JsonValueKind.String
                || !string.Equals(
                    reportedModel.GetString(),
                    _configuration.ModelName,
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "llama.cpp returned the wrong model identity.");
            }
            string candidate = root
                .GetProperty("choices")[0]
                .GetProperty("message")
                .GetProperty("content")
                .GetString() ?? "";
            candidate = Regex.Replace(
                candidate,
                @"<think>[\s\S]*?</think>",
                "",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant).Trim();
            if (candidate.Length == 0)
            {
                throw new InvalidOperationException("llama.cpp returned empty text.");
            }
            int inputTokens = Usage(root, "prompt_tokens");
            int outputTokens = Usage(root, "completion_tokens");
            _lastGeneration = DateTimeOffset.UtcNow;
            return new(
                candidate,
                inputTokens,
                outputTokens,
                timer.Elapsed.TotalSeconds);
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task WarmAsync(TimeSpan timeout)
    {
        await _gate.WaitAsync(timeout);
        try
        {
            await EnsureReadyAsync(timeout);
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task<RuntimeState> StatusAsync(TimeSpan timeout)
    {
        bool processAlive = _process is { HasExited: false };
        bool idleSleeping =
            _lastGeneration != default
            && DateTimeOffset.UtcNow - _lastGeneration
                >= TimeSpan.FromSeconds(_configuration.IdleSeconds);
        bool healthy = false;
        if (processAlive && _baseAddress is not null && !idleSleeping)
        {
            try
            {
                using var deadline = new CancellationTokenSource(timeout);
                using HttpResponseMessage response = await _http.GetAsync(
                    new Uri(_baseAddress, "/health"),
                    deadline.Token);
                healthy = response.IsSuccessStatusCode;
            }
            catch (Exception error) when (
                error is HttpRequestException or TaskCanceledException)
            {
                healthy = false;
            }
        }
        string detail = idleSleeping
            ? "Sleeping after five minutes idle; reloads on the next request."
            : healthy
                ? (_fullGpuResident || _configuration.Backend == "cpu"
                    ? "Ready"
                    : "GPU residency is not verified")
                : processAlive
                    ? "Loading"
                    : "Stopped";
        return new(
            _configuration.ModelName,
            healthy,
            _configuration.Backend,
            new FileInfo(_configuration.ModelPath).Length,
            _gpuBytes,
            detail,
            _configuration.Backend == "cpu" || _fullGpuResident,
            _loadStarted == default
                ? 0
                : Math.Max(0, (DateTimeOffset.UtcNow - _loadStarted).TotalSeconds));
    }

    private async Task EnsureReadyAsync(TimeSpan timeout)
    {
        if (_process is not null && _process.HasExited)
        {
            string detail = _safeLog.LastOrDefault() ?? "no safe detail";
            await StopAsync();
            throw new InvalidOperationException($"llama.cpp stopped: {detail}");
        }
        if (_process is null)
        {
            Start();
        }
        var timer = Stopwatch.StartNew();
        while (timer.Elapsed < timeout)
        {
            if (_process!.HasExited)
            {
                string detail = _safeLog.LastOrDefault() ?? "no safe detail";
                throw new InvalidOperationException($"llama.cpp failed to load: {detail}");
            }
            try
            {
                using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(1));
                using HttpResponseMessage response = await _http.GetAsync(
                    new Uri(_baseAddress!, "/health"),
                    deadline.Token);
                if (response.IsSuccessStatusCode)
                {
                    if (
                        _configuration.Backend != "cpu"
                        && !_fullGpuResident)
                    {
                        await StopAsync();
                        throw new InvalidOperationException(
                            "llama.cpp did not prove full GPU residency. CPU/GPU spill was rejected.");
                    }
                    return;
                }
            }
            catch (Exception error) when (
                error is HttpRequestException or TaskCanceledException)
            {
                // The private loopback server is still loading.
            }
            await Task.Delay(TimeSpan.FromMilliseconds(200));
        }
        await StopAsync();
        throw new TaskCanceledException("llama.cpp model loading exceeded the request limit.");
    }

    private void Start()
    {
        int port = FreeLoopbackPort();
        _apiKey = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
        _baseAddress = new Uri($"http://127.0.0.1:{port}");
        _loadStarted = DateTimeOffset.UtcNow;
        _lastGeneration = default;
        _gpuBytes = 0;
        _fullGpuResident = _configuration.Backend == "cpu";
        _captureLoadLogs = true;
        _safeLog.Clear();
        var start = new ProcessStartInfo(_configuration.ServerPath)
        {
            WorkingDirectory = _configuration.RuntimeRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        string[] arguments =
        [
            "--model", _configuration.ModelPath,
            "--alias", _configuration.ModelName,
            "--host", "127.0.0.1",
            "--port", port.ToString(),
            "--no-ui",
            "--parallel", "1",
            "--ctx-size", _configuration.ContextWindow.ToString(),
            "--sleep-idle-seconds", _configuration.IdleSeconds.ToString(),
            "--jinja",
            "--chat-template-kwargs", "{\"enable_thinking\":false}",
            "--no-context-shift",
            "--threads-http", "1",
            "--gpu-layers", _configuration.Backend == "cpu" ? "0" : "999",
            "-lv", "4",
        ];
        foreach (string value in arguments)
        {
            start.ArgumentList.Add(value);
        }
        start.Environment["LLAMA_API_KEY"] = _apiKey;
        _process = new Process { StartInfo = start, EnableRaisingEvents = true };
        _process.OutputDataReceived += CaptureSafeLog;
        _process.ErrorDataReceived += CaptureSafeLog;
        StartBoundToHost(_process);
        _process.BeginOutputReadLine();
        _process.BeginErrorReadLine();
    }

    internal static Process StartBoundToHost(ProcessStartInfo start)
    {
        var process = new Process { StartInfo = start, EnableRaisingEvents = true };
        StartBoundToHost(process);
        return process;
    }

    // Every child this host starts goes through here, so none can outlive it.
    internal static void StartBoundToHost(Process process)
    {
        if (!process.Start())
        {
            throw new InvalidOperationException("llama.cpp process did not start.");
        }
        try
        {
            ChildProcessJob.Assign(process);
        }
        catch (InvalidOperationException)
        {
            // A child that cannot be bound is exactly the orphan this exists
            // to prevent, holding gigabytes of VRAM after a crash. Refuse it:
            // the app then reports the host as unavailable and falls back,
            // which is visible, instead of leaking silently.
            try
            {
                process.Kill(entireProcessTree: true);
            }
            catch (InvalidOperationException)
            {
                // Already gone.
            }
            process.Dispose();
            throw;
        }
    }

    private void CaptureSafeLog(object sender, DataReceivedEventArgs eventArgs)
    {
        string value = eventArgs.Data ?? "";
        if (value.Length == 0 || !_captureLoadLogs)
        {
            return;
        }
        Match layers = Regex.Match(
            value,
            @"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers?\s+to\s+GPU",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        if (
            layers.Success
            && int.TryParse(layers.Groups[1].Value, out int loaded)
            && int.TryParse(layers.Groups[2].Value, out int total)
            && loaded > 0
            && loaded == total)
        {
            _fullGpuResident = true;
        }
        Match memory = Regex.Match(
            value,
            @"(?:CUDA|Vulkan|HIP|SYCL)\d*\s+[^=]*buffer size\s*=\s*([\d.]+)\s*MiB",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        if (
            memory.Success
            && double.TryParse(
                memory.Groups[1].Value,
                System.Globalization.CultureInfo.InvariantCulture,
                out double mib))
        {
            _gpuBytes += (long)(mib * 1024 * 1024);
        }
        string safe = value
            .Replace(_configuration.ModelPath, "<model>", StringComparison.OrdinalIgnoreCase)
            .Replace(_configuration.RuntimeRoot, "<runtime>", StringComparison.OrdinalIgnoreCase)
            .Replace(_apiKey, "<key>", StringComparison.Ordinal);
        if (safe.Length > 400)
        {
            safe = safe[..400];
        }
        lock (_safeLog)
        {
            _safeLog.Add(safe);
            if (_safeLog.Count > 40)
            {
                _safeLog.RemoveAt(0);
            }
        }
        if (value.Contains("model loaded", StringComparison.OrdinalIgnoreCase))
        {
            // Verbose logging is used only to prove startup residency. Ignore
            // every later llama.cpp line so writing can never enter host logs.
            _captureLoadLogs = false;
        }
    }

    private static int Usage(JsonElement root, string name)
    {
        return root.TryGetProperty("usage", out JsonElement usage)
            && usage.TryGetProperty(name, out JsonElement value)
            && value.TryGetInt32(out int result)
            ? Math.Max(0, result)
            : 0;
    }

    private static int FreeLoopbackPort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        int port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }

    private async Task StopAsync()
    {
        Process? process = _process;
        _process = null;
        _baseAddress = null;
        _apiKey = "";
        if (process is null)
        {
            return;
        }
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                await process.WaitForExitAsync();
            }
        }
        catch (InvalidOperationException)
        {
            // Process already stopped.
        }
        finally
        {
            process.Dispose();
        }
    }

    public async ValueTask DisposeAsync()
    {
        await StopAsync();
        _http.Dispose();
        _gate.Dispose();
    }
}

// Ends llama-server whenever this host ends, however it ends.
//
// Found 2026-10-01: force-stopping the host left llama-server.exe running
// with the model still in VRAM. Three orphans piled up, one at about 14 GB,
// and the next app starts came up rules-only because the fallback model
// could not fit on the GPU. The parent monitor only covers the APP exiting;
// a crashed or killed host never ran StopAsync.
//
// A job object created with KILL_ON_JOB_CLOSE ends every process in it when
// its last handle closes. The handle is created once and deliberately never
// closed: Windows closes it when this process exits, normally or not, and
// that is the moment the child must go.
internal static class ChildProcessJob
{
    private const int JobObjectExtendedLimitInformation = 9;
    private const uint JobObjectLimitKillOnJobClose = 0x2000;
    private static readonly Lazy<IntPtr> Job = new(Create);

    public static void Assign(Process process)
    {
        if (!AssignProcessToJobObject(Job.Value, process.Handle))
        {
            throw new InvalidOperationException(
                "The child process could not be bound to the host's lifetime " +
                $"(Windows error {Marshal.GetLastWin32Error()}).");
        }
    }

    private static IntPtr Create()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero)
        {
            throw new InvalidOperationException(
                $"The host job object could not be created (Windows error {Marshal.GetLastWin32Error()}).");
        }
        var limits = new JobObjectExtendedLimitInformationData
        {
            BasicLimitInformation = new JobObjectBasicLimitInformation
            {
                LimitFlags = JobObjectLimitKillOnJobClose,
            },
        };
        if (!SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ref limits,
            (uint)Marshal.SizeOf<JobObjectExtendedLimitInformationData>()))
        {
            throw new InvalidOperationException(
                $"The host job object could not be configured (Windows error {Marshal.GetLastWin32Error()}).");
        }
        return job;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectBasicLimitInformation
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectExtendedLimitInformationData
    {
        public JobObjectBasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string? name);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetInformationJobObject(
        IntPtr job,
        int informationClass,
        ref JobObjectExtendedLimitInformationData information,
        uint length);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
}

internal sealed record GenerationResponse(
    string Text,
    int InputTokens,
    int OutputTokens,
    double ElapsedSeconds);

internal sealed record RuntimeState(
    string Model,
    bool Loaded,
    string Backend,
    long ModelBytes,
    long GpuBytes,
    string Detail,
    bool FullGpuResident,
    double LoadElapsedSeconds);
