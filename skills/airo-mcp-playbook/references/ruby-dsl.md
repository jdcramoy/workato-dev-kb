# Ruby DSL (`ruby()` formula) restrictions

Load this file when writing a `ruby()` formula in any recipe step.

- **Restricted subset — no `.size`, `.dig`, `||`.** For nil-safe guards on
  deeply nested optional API response fields, prefer a `present`/`blank`
  unary condition operand directly on the datapill path — Workato resolves
  missing nested paths as blank without erroring — rather than a Ruby
  method call on a value that might be nil.
- **Multi-line variable assignment is not supported.** Compress logic into a
  single expression (nested ternaries, chained method calls) instead of
  intermediate variables. Confirmed via `docs_get(id="formulas:ruby-formulas")`.
- **String interpolation (`#{...}`) inside `ruby()` is rejected by the MCP
  tool itself** ("causes escaping issues"), even though it's normally valid
  Workato Ruby outside this tool path. Use string concatenation (`+`)
  instead.
