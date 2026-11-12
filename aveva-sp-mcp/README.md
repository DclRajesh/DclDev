# aveva-sp-mcp

An [MCP](https://modelcontextprotocol.io) server for **AVEVA System Platform** (Application
Server). It gives an AI coding assistant such as Claude Code a set of tools to read, edit, deploy
and test a Galaxy, so it can be used as a development tool alongside the IDE.

```
Claude Code ──MCP (stdio)──> aveva-sp-mcp.exe ──GRAccess──> Galaxy Repository   (configuration)
                                              └─MXAccess──> deployed objects    (live values)
```

> **Status: first version, not yet run against a real Galaxy.** The MCP server, the tools, the
> permission checks and the mock Galaxy are built and tested. The two files that talk to AVEVA
> (`Backends/Aveva/GRAccessGalaxyBackend.cs` and `MxAccessRuntimeBackend.cs`) were written from the
> toolkit documentation on a machine without System Platform, so they have only been compiled
> against stand-in stubs. Expect to fix a few signatures on the first real build; see
> [First build on a System Platform machine](#first-build-on-a-system-platform-machine).
> Try it on a development Galaxy first.

## Tools

| Area | Tools | Permission needed |
|---|---|---|
| Connection | `galaxy_status`, `galaxy_list`, `galaxy_connect` | none |
| Read the Galaxy | `object_query`, `object_get`, `object_attributes`, `object_scripts` | none |
| Create | `template_derive`, `instance_create` | `configure` |
| Edit | `attributes_set`, `uda_add`, `script_set`, `object_assign`, `object_checkout`, `object_checkin`, `object_undo_checkout` | `configure` |
| Delete | `object_delete` | `delete` |
| Deploy | `objects_deploy`, `objects_undeploy` | `deploy` |
| Live data | `runtime_read` | none |
| Live data | `runtime_write` | `runtime-write` |
| Export | `objects_export` (`.aaPKG` or galaxy-dump `.csv`) | none |
| Import | `package_import`, `csv_load` | `import` |

The edit tools each do a complete check-out, change, save and check-in. If any step fails the
check-out is undone, so a failed call does not leave objects checked out.

## Safety

The server acts on a real control system, so it **starts read-only**. Each kind of change is
switched on by whoever starts the server, through the `AVEVA_SP_ALLOW` environment variable. The
assistant cannot grant itself more access; a refused call tells it which permission is missing.

Suggested levels:

| Use | `AVEVA_SP_ALLOW` |
|---|---|
| Exploring / documenting a Galaxy | *(empty)* |
| Development on a dev Galaxy | `configure,deploy,import` |
| Everything, on a test system only | `all` |

`runtime-write` changes live values and can start or stop equipment. Leave it off on anything
connected to plant.

Exports and imports are confined to one folder (`AVEVA_SP_TRANSFER_DIR`); file names that point
outside it are refused.

## Configuration

All settings are environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `AVEVA_SP_NODE` | this machine | Galaxy Repository node |
| `AVEVA_SP_GALAXY` | *(none)* | Galaxy to log in to on first use. Without it, call `galaxy_connect`. |
| `AVEVA_SP_USER`, `AVEVA_SP_PASSWORD` | empty | Galaxy login. Leave empty when Galaxy security is off. |
| `AVEVA_SP_ALLOW` | empty (read-only) | Comma-separated: `configure`, `delete`, `deploy`, `import`, `runtime-write`, or `all` |
| `AVEVA_SP_TRANSFER_DIR` | `%TEMP%\aveva-sp-mcp` | Folder for exported and imported files |
| `AVEVA_SP_BACKEND` | `auto` | `graccess`, `mock`, or `auto` (GRAccess when compiled in, otherwise mock) |

## Build

Requirements on the System Platform machine (GR node or a node with the IDE installed):

- AVEVA System Platform with the IDE, which installs `ArchestrA.GRAccess.dll` and `ArchestrA.MxAccess.dll`
- [.NET SDK 8](https://dotnet.microsoft.com/download) or later (it builds the .NET Framework 4.8 target too)

```bat
cd aveva-sp-mcp
dotnet build src\AvevaSpMcp -c Release -f net48
```

The build prints whether it found the AVEVA libraries:

```
aveva-sp-mcp: GRAccess backend compiled in = 'true' (C:\Program Files (x86)\Common Files\ArchestrA\ArchestrA.GRAccess.dll)
aveva-sp-mcp: MXAccess backend compiled in = 'true' (C:\Program Files (x86)\ArchestrA\Framework\Bin\ArchestrA.MxAccess.dll)
```

If one says `''`, the DLL is somewhere else on your install. Find it and pass the path:

```bat
dotnet build src\AvevaSpMcp -c Release -f net48 -p:GRAccessDll="D:\path\ArchestrA.GRAccess.dll" -p:MxAccessDll="D:\path\ArchestrA.MxAccess.dll"
```

The result is `src\AvevaSpMcp\bin\Release\net48\aveva-sp-mcp.exe` (32-bit, because the AVEVA
libraries are).

## Use with Claude Code

On the same machine, register the server (adjust the path and Galaxy name):

```bat
claude mcp add aveva-sp ^
  -e AVEVA_SP_GALAXY=MyDevGalaxy ^
  -e AVEVA_SP_ALLOW=configure,deploy,import ^
  -- C:\path\to\aveva-sp-mcp\src\AvevaSpMcp\bin\Release\net48\aveva-sp-mcp.exe
```

or copy [`mcp.example.json`](mcp.example.json) to `.mcp.json` in the project you work in. Then ask,
for example:

- "List the templates derived from `$UserDefined` and summarise what each script does."
- "Add a `RunHours` UDA and a script that accumulates it while `Running` is true to `$Pump`."
- "Create `Pump_010` to `Pump_015` from `$Pump` in `Area_PumpStation`, deploy them, and check `SpeedSP` reads good quality."
- "Export `$Pump` and its instances to an aaPKG before we change anything."

Run the account that starts Claude Code as a user that is allowed to use the IDE on that Galaxy.

## Try it without System Platform

On any machine (Windows, Linux, macOS) the `net8.0` build runs with a small in-memory mock Galaxy
(`$Pump`, `Pump_001`, `Pump_002`, an area, an engine and a platform):

```bash
dotnet build src/AvevaSpMcp -f net8.0
claude mcp add aveva-sp-mock -e AVEVA_SP_ALLOW=all -- dotnet /full/path/to/src/AvevaSpMcp/bin/Debug/net8.0/aveva-sp-mcp.dll
dotnet test          # 12 tests, all against the mock
```

This is for trying the tools and for developing the server. The mock is not a simulator.

## First build on a System Platform machine

The AVEVA-facing code could not be compiled against the real libraries where it was written. It is
designed so that most differences show up as clear run-time messages rather than build errors
(enum members are looked up by name, descriptive properties are read by reflection). What is left
is marked `VERIFY` in the source. Suggested order:

1. Build. Fix any compile errors in `Backends/Aveva/` against the real interop signatures
   (Visual Studio's Object Browser on `ArchestrA.GRAccess.dll` shows them). The most likely one is
   `IGalaxy.QueryObjects`, whose value parameter is `ref object` in some toolkit versions.
2. Read-only checks with `AVEVA_SP_ALLOW` empty: `galaxy_list`, `galaxy_connect`, `object_query`,
   `object_get`, `object_attributes`, `object_scripts`.
3. On a scratch template in a dev Galaxy with `AVEVA_SP_ALLOW=configure`: `template_derive`,
   `uda_add`, `attributes_set`, `script_set`, `instance_create`, `object_assign`.
4. `objects_export`, then `deploy`, `runtime_read`, and last `runtime-write`.

Doing this with Claude Code on that machine works well: it can read the compiler errors and the
`VERIFY` notes and correct the backend against the real DLL.

### Known gaps

- Array attributes read as `<not readable as a scalar>` and cannot be set.
- Settable types: boolean, integer, float, double, strings and references. Time, elapsed time,
  enums and qualified structs are not settable yet.
- `runtime_write` writes without a user identity, so attributes secured above *Operate* (verified
  write, tune, configure) will be refused by the platform.
- `object_query` sends one condition to the Galaxy Repository and filters the rest in memory, so
  a broad query on a very large Galaxy is slow. Use `derivedFrom`, `area` or `host` to narrow it.
- No graphics (ArchestrA symbols), no Galaxy backup/restore, no security configuration.

## Layout

```
src/AvevaSpMcp/
  Program.cs                     server start-up, backend selection
  ServerOptions.cs               environment settings, AccessPolicy (permissions, transfer folder)
  Models.cs                      data returned by tools
  Tools/Tools.cs                 the MCP tools (the only layer that checks permissions)
  Backends/Interfaces.cs         IGalaxyBackend, IRuntimeBackend
  Backends/Aveva/                GRAccess and MXAccess implementations (net48 only)
  Backends/Mock/                 in-memory Galaxy
  Backends/StaWorker.cs          single STA thread that all COM calls run on
tests/AvevaSpMcp.Tests/          tool-level tests against the mock
```

To add a tool: add the operation to `IGalaxyBackend`, implement it in both backends, and expose it
in `Tools/Tools.cs` with a `[Description]` that says what it changes and which permission it needs.
