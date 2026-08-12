# Tool contract

- Module location: `webPage/tools/<name>.py`.
- Name: lowercase letters, digits, `-`, `_`; unique across Tools.
- Decorator: `register_web_tool(name=..., title=..., description=..., fields=[...])`.
- Body signature: one `dict[str, object]` argument.
- Result: any JSON-serializable value; return structured dictionaries for readable rendering.
- Field keys: `name`, `label`, `type`, `required`, optionally `placeholder`, `options`, `default`.
- The Web server imports tools at discovery time. Do not perform work, access the network, or mutate files at module import.
- Raise `ValueError` for user input errors. Do not expose secrets in returned values.
