# Release notes

## v1.3.1 — 2026-11-02

Reliability update built from the ZeroDay source tree.

### Changes

- Bounded numeric parsing handles null input and overflow.
- File-watcher removal, shared-directory registrations and shutdown handling improved; shutdown no longer intercepts process-wide SIGINT.
- Invalidated directory watches retry attachment every 250 ms. Removing a pending watch cancels retries.
- Lua initialization attempts to restart a stopped watcher with existing registrations.
- Subprocess capture works when the parent's stdout is closed.
- Helper execution has a 185-second default deadline covering output capture and child exit. Timeout clears captured output, reports `ETIMEDOUT`, terminates the local helper process group and reaps the direct child.
- Logging macros, custom trace dispatch and library-index format strings corrected.
- Includes the previously local HTTP-error body handling and logging-severity corrections.

### Validation

- Final helper suite: 50/50 checks across release and debug-trace modes.
- Release packaging: 31/31 isolated installer tests passed, with shell syntax and payload-hash checks.
- Follow-up sanitizer checks: 25/25 address/undefined-behavior checks and 4/4 targeted thread checks.
- Local startup after installation: new library mapped, 41/41 hooks placed, zero error-level startup entries.
- Native-client ABI/pattern/trampoline tests passed on the preceding local build; they were not rerun for this final build. Startup validation is not an exhaustive runtime feature test.
- Build succeeded with nonnull-comparison and decompiler format warnings.

Watch events during an unattached interval are not replayed. Lua watcher recovery occurs during initialization, not through a continuous watchdog; this recovery path was reviewed and syntax-checked rather than exercised in a live Lua VM. Timeout cleanup does not guarantee termination of detached descendants or commands launched through a separate host service.

### Build identity

- `ZeroDay.so` SHA-256: `5203f52f2686229367843050f667711870819b8241cff90750bd87a533247753`
- Injector unchanged. Steam client compatibility policy remains build `1792618285` with the existing exact client hashes.
- No upstream UDP packet hook was imported. Earlier review claims that `AdditionalPackageApps`, ticket-splicing markers and auxiliary tools were new to the latest upstream debug archive were incorrect; they were present in the older archive too.

Release assets include the installer, payload archive and `SHA256SUMS`.
