# Static review checklist

- Archive expands below 128 MiB and 2,000 files.
- No absolute path, `..`, link, device, executable, DLL, environment file, private key, or credential file.
- No `eval`, `exec`, unsafe pickle loading, unbounded decompression, or import-time work.
- No subprocess, network, remote connection, destructive file operation, or privilege requirement in the Web Tool path.
- Inputs fit the AutoEnv Tool field contract; output supports JSON serialization and offline UT.
- Licensing permits reuse and required notices are preserved.
