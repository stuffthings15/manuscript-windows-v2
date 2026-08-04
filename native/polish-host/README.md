# Manuscript Polish Host

This current-user broker launches one verified `llama-server.exe` package and
exposes only Manuscript's bounded, length-prefixed JSON protocol over a Windows
named pipe.

Security boundaries:

- `PipeOptions.CurrentUserOnly`
- random 256-bit per-launch pipe secret
- parent-process lifetime monitoring
- fixed local model/runtime roots
- strict request and response limits
- authenticated loopback child server with its UI disabled
- no writing or prompts in logs
- full-GPU residency required for GPU plans
- llama.cpp five-minute idle sleep

The host is published self-contained and bundled with Manuscript. llama.cpp
backend binaries remain separately downloaded, signed-manifest packages.
