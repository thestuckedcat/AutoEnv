# Tool contract

- Module location: `webPage/tools/<name>.py`.
- Registration template: copy `webPage/tools/_template.py` to a non-underscore filename and replace every placeholder. The leading underscore keeps the template out of automatic discovery.
- Name: lowercase letters, digits, `-`, `_`; unique across Tools.
- Decorator: `register_web_tool(name=..., title=..., description=..., fields=[...], kind=..., renderer=...)`.
- `kind="local"` (default): body signature is one `dict[str, object]`; result is JSON-serializable and runs in the HTTP process.
- `kind="workflow"`: body signature is one `RunContext`; the Web bridge runs it in a dedicated child process with event polling and stop support. Return `None` or an AutoEnv result object.
- Workflow resources are inferred from literal `ctx.register_ssh_host()`, `ctx.register_telnet()`, and `ctx.register_ftp_host()` calls, then bound through the same environment-label contract as scripts.
- Tools and environment scripts have independent registries. A Tool must never be returned by `list_scripts()`.
- `renderer="json"` is the local default. Reusable workflow renderers (currently `log_collection`) may add dedicated Web query behavior.
- Field keys: `name`, `label`, `type`, `required`, optionally `placeholder`, `options`, `default`.
- The Web server imports tools at discovery time. Do not perform work, access the network, or mutate files at module import.
- Every non-underscore `.py` file is a live discovery candidate and must register exactly one Tool. Underscore files are support/template modules and must not register a visible Tool at runtime.
- Local Tools do not access network or devices. Workflow Tools access only declared bound resources through the AutoEnv SDK; do not directly use Paramiko, socket, shell subprocesses, or credentials.
- Raise `ValueError` for user input errors. Do not expose secrets in returned values.
