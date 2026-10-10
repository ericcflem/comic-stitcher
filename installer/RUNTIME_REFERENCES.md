# Runtime integration references — checked 8 October 2026

Official Microsoft download and architecture/version requirements:
https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist
https://aka.ms/vc14/vc_redist.x64.exe

Microsoft deployment, licensed publisher/redistribution requirements, interactive
and silent command-line options, and servicing guidance:
https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files

Inno prerequisite callback and pre-install restart behavior:
https://jrsoftware.org/ishelp/topic_scriptevents.htm

Inno system-directory constants and 32-bit Setup behavior:
https://jrsoftware.org/ishelp/topic_consts.htm
https://jrsoftware.org/ishelp/topic_setup_setuparchitecture.htm

Microsoft's installer is verified on Windows using Authenticode before compile.
Its normal UI is used; its agreement is not silently accepted by the application.
These technical references do not establish complete redistribution clearance.
